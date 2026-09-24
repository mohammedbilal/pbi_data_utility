"""Expectation store — the util_* mirror tables behind the Email Comparison
feature (spec section 18).

The utility writes the *expected* database state for every generated email here,
then the rows the agent actually produced are imported alongside them and diffed.
All three lanes (expected / baseline / actual) live in the same mirror tables,
discriminated by ``util_source``, so a comparison is a self-join.

Design notes:

* **SQLite, one file** (``backend/expected/pbi_util.db``). No credentials and no
  network, so capture works in ``dry_run`` / ``email_only`` and the utility can
  never write to the environment under test. See spec 18.3.
* **DDL is generated from the schema exports** in ``reference/db_schema/``
  (``*_fields_size.csv``), so a new application column is picked up by dropping
  in a refreshed export — no code change. Column names are byte-identical to the
  app's; utility metadata rides in ``util_``-prefixed extra columns.
* **Thread-safe.** Engines run on background threads, so every operation takes a
  module lock and its own connection (WAL mode). Mirrors ``history_manager.py``.
* **Length validation** against ``character_maximum_length``: over-length values
  are returned as warnings for the caller to log (spec 18.4) — never silently
  truncated, and never fatal.

Nothing in here knows how to *build* an expectation (that is
``engines/expected_writer.py``) or how to compare one (``comparators.py``).
"""
from __future__ import annotations

import csv
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from paths import BACKEND_DIR, STATE_DIR

SCHEMA_DIR = BACKEND_DIR / "reference" / "db_schema"
DEFAULT_STORE_PATH = STATE_DIR / "expected" / "pbi_util.db"

# app table (as exported)  ->  mirror table
MIRROR_TABLES: Dict[str, str] = {
    "issuance_deal": "util_issuance_deal",
    "issuance_data": "util_issuance_data",
    "issuance": "util_issuance",
    "issuance_security": "util_issuance_security",
}

SOURCES = ("expected", "baseline", "actual")
ASSET_CLASSES = ("bonds", "loans", "abs", "munis")

# Utility metadata added to every mirror row. util_ prefix guarantees no clash
# with a present or future application column.
META_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("util_row_id", "TEXT PRIMARY KEY"),
    ("util_run_id", "TEXT"),
    ("util_email_id", "TEXT"),
    ("util_source", "TEXT"),
    ("util_asset_class", "TEXT"),
    ("util_deal_seq", "INTEGER"),
    ("util_tranche_seq", "INTEGER"),
    ("util_match_key", "TEXT"),
    ("util_created_at", "TEXT"),
)
META_NAMES = tuple(name for name, _ in META_COLUMNS)

_lock = threading.RLock()
_schema_cache: Dict[str, Dict[str, Optional[int]]] = {}
_store_path_override: Optional[Path] = None


# ── schema (from the information_schema exports) ──────────────────────────────

def load_schema(table: str) -> Dict[str, Optional[int]]:
    """{column_name: character_maximum_length or None}, in DB ordinal order."""
    if table in _schema_cache:
        return _schema_cache[table]
    if table not in MIRROR_TABLES:
        raise KeyError(f"Unknown table '{table}'. Expected one of {sorted(MIRROR_TABLES)}.")
    path = SCHEMA_DIR / f"{table}_fields_size.csv"
    if not path.exists():
        raise FileNotFoundError(f"Schema export missing: {path}")
    cols: Dict[str, Optional[int]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("column_name") or "").strip()
            if not name:
                continue
            raw = (row.get("character_maximum_length") or "").strip()
            try:
                cols[name] = int(raw) if raw and raw.upper() != "NULL" else None
            except ValueError:
                cols[name] = None
    if not cols:
        raise ValueError(f"Schema export is empty: {path}")
    _schema_cache[table] = cols
    return cols


def app_columns(table: str) -> List[str]:
    return list(load_schema(table).keys())


# ── field / vocab map (seed CSVs + overrides) ─────────────────────────────────

def load_field_map(asset_class: str = "bonds") -> Dict[Tuple[str, str], Dict[str, str]]:
    """{(table, column): {source_kind, source, tier, normalizer, notes}} from the seed CSV."""
    path = SCHEMA_DIR / "field_map.csv"
    out: Dict[Tuple[str, str], Dict[str, str]] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("asset_class") or "").strip() != asset_class:
                continue
            key = ((row.get("table") or "").strip(), (row.get("column") or "").strip())
            if not key[0] or not key[1]:
                continue
            out[key] = {
                "source_kind": (row.get("source_kind") or "").strip(),
                "source": (row.get("source") or "").strip(),
                "tier": (row.get("tier") or "x").strip(),
                "normalizer": (row.get("normalizer") or "").strip(),
                "notes": (row.get("notes") or "").strip(),
            }
    return out


def load_vocab_map(asset_class: str = "bonds") -> Dict[Tuple[str, str], Dict[str, str]]:
    """{(column, generated_value_lower): {db_value, confirmed, notes}} from the seed CSV."""
    path = SCHEMA_DIR / "vocab_map.csv"
    out: Dict[Tuple[str, str], Dict[str, str]] = {}
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("asset_class") or "").strip() != asset_class:
                continue
            col = (row.get("column") or "").strip()
            gen = (row.get("generated_value") or "").strip()
            if not col or not gen:
                continue
            out[(col, gen.casefold())] = {
                "db_value": (row.get("db_value") or "").strip(),
                "confirmed": (row.get("confirmed") or "no").strip(),
                "notes": (row.get("notes") or "").strip(),
            }
    return out


def effective_map(asset_class: str = "bonds") -> Dict[str, Any]:
    """The field and vocab maps, as ``{"fields": {...}, "vocab": {...}}``.

    Kept as a function (rather than callers reading the CSVs directly) because
    ``expected_writer`` and ``comparators`` both need the same view, and because
    it is the one place to change if the maps ever move. When an expectation
    turns out to be wrong the fix is to edit ``field_map.csv`` or
    ``vocab_map.csv`` — there is deliberately no override layer.
    """
    return {"fields": load_field_map(asset_class),
            "vocab": load_vocab_map(asset_class)}


# ── connection / DDL ─────────────────────────────────────────────────────────

def set_store_path(path: Optional[str | Path]) -> None:
    """Point the store at an explicit file (``compare.store_path``)."""
    global _store_path_override
    _store_path_override = Path(path) if path else None


def store_path() -> Path:
    return _store_path_override or DEFAULT_STORE_PATH


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect() -> sqlite3.Connection:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _read(sql: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    """Run a read query, degrading to [] when the store isn't there yet.

    Reads must never require ``init_store()`` to have run: ``effective_map`` is
    called by the pure projection layer and by the comparator, and on a machine
    that has never captured anything the answer is simply "no overrides". Only
    writes create the store. A missing *table* is tolerated for the same reason
    (a store file created by an interrupted first run).
    """
    if not store_path().exists():
        return []
    with _lock:
        conn = _connect()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
        finally:
            conn.close()


_SUPPORT_DDL = (
    """CREATE TABLE IF NOT EXISTS util_run (
         util_run_id TEXT PRIMARY KEY, ts TEXT, tool TEXT, util_asset_class TEXT,
         env TEXT, host TEXT, email_format TEXT, email_mode TEXT, ticker TEXT,
         deals INTEGER, tranches INTEGER, params_json TEXT, notes TEXT)""",
    """CREATE TABLE IF NOT EXISTS util_email (
         util_email_id TEXT PRIMARY KEY, util_run_id TEXT, util_deal_seq INTEGER,
         ts TEXT, subject TEXT, body_html TEXT, email_format TEXT, recipient TEXT,
         send_status TEXT, send_note TEXT, msg_path TEXT, rendered_fields TEXT,
         ticker TEXT, tranches INTEGER)""",
    # One label per uploaded email (spec 19.9). Kept as a blob rather than
    # reverse-engineered out of the mirror rows, so a label can be reopened and
    # edited, and so the effort is exportable — which is what makes 20-50 emails a
    # one-time cost rather than a recurring one.
    """CREATE TABLE IF NOT EXISTS util_email_label (
         util_email_id TEXT PRIMARY KEY, util_run_id TEXT, label_json TEXT,
         label_status TEXT, rendered_fields TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS util_comparison (
         comparison_id TEXT PRIMARY KEY, util_run_id TEXT, llm_run_id TEXT,
         ts TEXT, against TEXT, three_way INTEGER, scores_json TEXT,
         assertions_json TEXT, notes TEXT)""",
    """CREATE TABLE IF NOT EXISTS util_comparison_field (
         finding_id TEXT PRIMARY KEY, comparison_id TEXT, util_run_id TEXT,
         "table" TEXT, column_name TEXT, tier TEXT, normalizer TEXT,
         in_email INTEGER, util_deal_seq INTEGER, util_tranche_seq INTEGER,
         match_confidence TEXT, expected_value TEXT, baseline_value TEXT,
         actual_value TEXT, verdict TEXT, note TEXT,
         resolved INTEGER, resolution TEXT)""",
)

_SUPPORT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_email_run ON util_email(util_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_cmp_run ON util_comparison(util_run_id)",
    "CREATE INDEX IF NOT EXISTS ix_cmpf_cmp ON util_comparison_field(comparison_id)",
    "CREATE INDEX IF NOT EXISTS ix_cmpf_col ON util_comparison_field(\"table\", column_name)",
    "CREATE INDEX IF NOT EXISTS ix_cmpf_verdict ON util_comparison_field(verdict, resolved)",
)

# Columns added to a support table after a store already existed in the wild.
# SQLite's CREATE TABLE IF NOT EXISTS will not add them, so init_store patches
# them in — a store captured in S2/S3 keeps its rows and gains the columns.
_ADDED_COLUMNS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "util_comparison_field": (("normalizer", "TEXT"), ("note", "TEXT")),
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        try:
            have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            continue
        if not have:
            continue
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {_quote(name)} {decl}")


def _mirror_ddl(table: str) -> str:
    """CREATE TABLE for one mirror table: util metadata + every app column as TEXT.

    Everything is TEXT deliberately — the store keeps values exactly as generated
    or fetched, and normalisation/typing is the comparator's job (spec 18.8).
    SQLite's dynamic typing means a numeric fetched from Postgres round-trips
    without being coerced behind our back.
    """
    cols = [f"{_quote(n)} {t}" for n, t in META_COLUMNS]
    cols += [f"{_quote(c)} TEXT" for c in app_columns(table)]
    return f"CREATE TABLE IF NOT EXISTS {MIRROR_TABLES[table]} (\n  " + ",\n  ".join(cols) + "\n)"


def init_store() -> Path:
    """Create the store and every table if absent. Idempotent."""
    with _lock:
        conn = _connect()
        try:
            for table in MIRROR_TABLES:
                conn.execute(_mirror_ddl(table))
                mt = MIRROR_TABLES[table]
                conn.execute(f"CREATE INDEX IF NOT EXISTS ix_{mt}_run "
                             f"ON {mt}(util_run_id, util_source)")
                conn.execute(f"CREATE INDEX IF NOT EXISTS ix_{mt}_key "
                             f"ON {mt}(util_match_key)")
            for ddl in _SUPPORT_DDL:
                conn.execute(ddl)
            _migrate(conn)
            for ddl in _SUPPORT_INDEXES:
                conn.execute(ddl)
            conn.commit()
        finally:
            conn.close()
    return store_path()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:16]


# ── mirror rows ──────────────────────────────────────────────────────────────

def validate_lengths(table: str, row: Dict[str, Any]) -> List[str]:
    """Values longer than character_maximum_length (spec 18.4). Never truncates."""
    schema = load_schema(table)
    warnings: List[str] = []
    for col, value in row.items():
        if col in META_NAMES or value is None:
            continue
        limit = schema.get(col)
        if limit is None:
            continue
        text = value if isinstance(value, str) else str(value)
        if len(text) > limit:
            warnings.append(
                f"{table}.{col}: {len(text)} chars exceeds varchar({limit}) — "
                f"the app will truncate or reject: {text[:40]!r}…")
    return warnings


def _coerce(value: Any) -> Optional[str]:
    """Store everything as TEXT, preserving bools as true/false and dropping NaN."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value != value:   # NaN
        return None
    return str(value)


def insert_rows(table: str, rows: Sequence[Dict[str, Any]], *,
                util_run_id: str, util_source: str,
                util_asset_class: str = "bonds",
                util_email_id: Optional[str] = None,
                validate: bool = True) -> Tuple[int, List[str]]:
    """Insert mirror rows. Returns (inserted, length warnings).

    Each row is a mapping of **app column names** plus optional
    ``util_deal_seq`` / ``util_tranche_seq`` / ``util_match_key`` /
    ``util_email_id``. Unknown keys are rejected loudly — a typo in a column
    name would otherwise silently drop an expectation.
    """
    if util_source not in SOURCES:
        raise ValueError(f"util_source must be one of {SOURCES}, got {util_source!r}")
    if not rows:
        return 0, []

    allowed = set(app_columns(table))
    per_row_meta = {"util_deal_seq", "util_tranche_seq", "util_match_key", "util_email_id"}
    warnings: List[str] = []
    payloads: List[Dict[str, Any]] = []

    for row in rows:
        unknown = set(row) - allowed - per_row_meta
        if unknown:
            raise KeyError(f"{table}: unknown column(s) {sorted(unknown)}")
        if validate:
            warnings.extend(validate_lengths(table, row))
        rec: Dict[str, Any] = {
            "util_row_id": _new_id(),
            "util_run_id": util_run_id,
            "util_email_id": row.get("util_email_id", util_email_id),
            "util_source": util_source,
            "util_asset_class": util_asset_class,
            "util_deal_seq": row.get("util_deal_seq"),
            "util_tranche_seq": row.get("util_tranche_seq"),
            "util_match_key": row.get("util_match_key"),
            "util_created_at": _now(),
        }
        for col in allowed:
            if col in row:
                rec[col] = _coerce(row[col])
        payloads.append(rec)

    mt = MIRROR_TABLES[table]
    with _lock:
        conn = _connect()
        try:
            for rec in payloads:
                cols = list(rec.keys())
                sql = (f"INSERT INTO {mt} ({', '.join(_quote(c) for c in cols)}) "
                       f"VALUES ({', '.join('?' * len(cols))})")
                conn.execute(sql, [rec[c] for c in cols])
            conn.commit()
        finally:
            conn.close()
    return len(payloads), warnings


def query_rows(table: str, *, util_run_id: Optional[str] = None,
               util_source: Optional[str] = None,
               util_deal_seq: Optional[int] = None,
               util_asset_class: Optional[str] = None) -> List[Dict[str, Any]]:
    mt = MIRROR_TABLES[table]
    where, args = [], []
    for col, val in (("util_run_id", util_run_id), ("util_source", util_source),
                     ("util_deal_seq", util_deal_seq),
                     ("util_asset_class", util_asset_class)):
        if val is not None:
            where.append(f"{_quote(col)} = ?")
            args.append(val)
    sql = f"SELECT * FROM {mt}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY util_deal_seq, util_tranche_seq, util_created_at"
    return _read(sql, args)


def count_rows(table: str, **filters: Any) -> int:
    return len(query_rows(table, **filters))


def row_counts(util_run_id: Optional[str] = None) -> Dict[str, Dict[str, int]]:
    """``{table: {source: n}}`` in one pass — the runs list needs this per run,
    and counting through ``query_rows`` would fetch every column of every row."""
    out: Dict[str, Dict[str, int]] = {t: {s: 0 for s in SOURCES} for t in MIRROR_TABLES}
    for table, mt in MIRROR_TABLES.items():
        sql = f"SELECT util_source AS src, COUNT(*) AS n FROM {mt}"
        args: List[Any] = []
        if util_run_id:
            sql += " WHERE util_run_id = ?"
            args.append(util_run_id)
        sql += " GROUP BY util_source"
        for row in _read(sql, args):
            if row["src"] in out[table]:
                out[table][row["src"]] = int(row["n"] or 0)
    return out


def delete_rows(table: str, *, util_run_id: str, util_source: str) -> int:
    """Drop one lane of one table for a run — so a re-fetch or re-import
    replaces rather than duplicates. Never touches the ``expected`` lane unless
    the caller explicitly asks for it."""
    if util_source not in SOURCES:
        raise ValueError(f"util_source must be one of {SOURCES}, got {util_source!r}")
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                f"DELETE FROM {MIRROR_TABLES[table]} "
                f"WHERE util_run_id = ? AND util_source = ?", (util_run_id, util_source))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def export_csv(table: str, **filters: Any) -> str:
    """Mirror rows as CSV text — app columns only, in DB ordinal order, so the
    output is diffable against an ``information_schema``-ordered app export."""
    rows = query_rows(table, **filters)
    cols = app_columns(table)
    out: List[str] = []
    writer = csv.writer(_ListWriter(out), quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(cols)
    for row in rows:
        writer.writerow(["NULL" if row.get(c) is None else row[c] for c in cols])
    return "".join(out)


class _ListWriter:
    """Minimal file-like sink so csv.writer can build a string."""

    def __init__(self, sink: List[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> int:
        self._sink.append(text)
        return len(text)


# ── runs / emails ────────────────────────────────────────────────────────────

_RUN_FIELDS = ("tool", "util_asset_class", "env", "host", "email_format",
               "email_mode", "ticker", "deals", "tranches", "notes")


def add_run(record: Dict[str, Any]) -> str:
    run_id = str(record.get("util_run_id") or _new_id())
    rec = {"util_run_id": run_id, "ts": record.get("ts") or _now(),
           "params_json": json.dumps(record.get("params") or {}, default=str)}
    for f in _RUN_FIELDS:
        rec[f] = _coerce(record.get(f))
    if not rec.get("util_asset_class"):
        rec["util_asset_class"] = "bonds"
    with _lock:
        conn = _connect()
        try:
            cols = list(rec.keys())
            conn.execute(
                f"INSERT OR REPLACE INTO util_run ({', '.join(_quote(c) for c in cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})", [rec[c] for c in cols])
            conn.commit()
        finally:
            conn.close()
    return run_id


def update_run(util_run_id: str, **fields: Any) -> None:
    allowed = set(_RUN_FIELDS) | {"deals", "tranches"}
    sets = {k: _coerce(v) for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                f"UPDATE util_run SET {', '.join(f'{_quote(k)} = ?' for k in sets)} "
                f"WHERE util_run_id = ?", [*sets.values(), util_run_id])
            conn.commit()
        finally:
            conn.close()


def new_run_id() -> str:
    """A run id for a caller that needs it *before* the run row exists — an
    uploaded email set names its file directory after it (spec 19.5)."""
    return _new_id()


def set_run_params(util_run_id: str, params: Dict[str, Any]) -> None:
    """Replace a run's ``params_json`` wholesale.

    ``update_run`` allowlists scalar columns on purpose; params is a blob, so it
    gets its own setter. An upload run keeps its parsed manifest here — which is
    also where a hand-pinned identifier will be recorded, so this stays a single
    read-modify-write rather than growing columns.
    """
    with _lock:
        conn = _connect()
        try:
            conn.execute("UPDATE util_run SET params_json = ? WHERE util_run_id = ?",
                         (json.dumps(params or {}, default=str), util_run_id))
            conn.commit()
        finally:
            conn.close()


def list_runs(util_asset_class: Optional[str] = None,
              limit: int = 200) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM util_run"
    args: List[Any] = []
    if util_asset_class:
        sql += " WHERE util_asset_class = ?"
        args.append(util_asset_class)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(limit))
    return _read(sql, args)


def get_run(util_run_id: str) -> Optional[Dict[str, Any]]:
    rows = _read("SELECT * FROM util_run WHERE util_run_id = ?", (util_run_id,))
    return rows[0] if rows else None


_EMAIL_FIELDS = ("util_run_id", "util_deal_seq", "subject", "body_html",
                 "email_format", "recipient", "send_status", "send_note",
                 "msg_path", "ticker", "tranches")


def add_email(record: Dict[str, Any]) -> str:
    email_id = str(record.get("util_email_id") or _new_id())
    rec: Dict[str, Any] = {"util_email_id": email_id, "ts": record.get("ts") or _now()}
    for f in _EMAIL_FIELDS:
        rec[f] = _coerce(record.get(f))
    rendered = record.get("rendered_fields")
    rec["rendered_fields"] = json.dumps(sorted(rendered)) if rendered else None
    with _lock:
        conn = _connect()
        try:
            cols = list(rec.keys())
            conn.execute(
                f"INSERT OR REPLACE INTO util_email ({', '.join(_quote(c) for c in cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})", [rec[c] for c in cols])
            conn.commit()
        finally:
            conn.close()
    return email_id


def set_email_label(util_email_id: str, util_run_id: str, label: Dict[str, Any],
                    *, rendered_fields: Optional[Sequence[str]] = None,
                    status: str = "labelled") -> None:
    """Store (or replace) one uploaded email's label — spec 19.9."""
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO util_email_label "
                "(util_email_id, util_run_id, label_json, label_status, "
                " rendered_fields, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (util_email_id, util_run_id, json.dumps(label or {}, default=str),
                 status, json.dumps(sorted(rendered_fields or [])), _now()))
            conn.commit()
        finally:
            conn.close()


def get_email_label(util_email_id: str) -> Optional[Dict[str, Any]]:
    rows = _read("SELECT * FROM util_email_label WHERE util_email_id = ?",
                 (util_email_id,))
    return _decode_label(rows[0]) if rows else None


def list_email_labels(util_run_id: str) -> List[Dict[str, Any]]:
    return [_decode_label(r) for r in
            _read("SELECT * FROM util_email_label WHERE util_run_id = ?",
                  (util_run_id,))]


def _decode_label(row: Dict[str, Any]) -> Dict[str, Any]:
    for key, default in (("label_json", {}), ("rendered_fields", [])):
        try:
            row[key] = json.loads(row.get(key) or "null")
            if row[key] is None:
                row[key] = default
        except (TypeError, ValueError):
            row[key] = default
    row["label"] = row.pop("label_json")
    return row


def delete_email_label(util_email_id: str) -> None:
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM util_email_label WHERE util_email_id = ?",
                         (util_email_id,))
            conn.commit()
        finally:
            conn.close()


def delete_rows_for_email(util_email_id: str, util_source: str = "expected") -> int:
    """Drop one email's rows from all four mirrors.

    Re-saving a label **replaces** that email's expectation (19.9), and this is how:
    scoped to the email rather than the run, so relabelling one email of thirty
    cannot disturb the other twenty-nine.
    """
    deleted = 0
    with _lock:
        conn = _connect()
        try:
            for mirror in MIRROR_TABLES.values():
                cur = conn.execute(
                    f"DELETE FROM {mirror} WHERE util_email_id = ? AND util_source = ?",
                    (util_email_id, util_source))
                deleted += cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
    return deleted


def known_tickers() -> List[str]:
    """Every ticker any capture run has already used.

    Seeds the bonds engine's ticker minter so a new run cannot reuse an earlier
    run's ticker — that is what keeps a run's ticker set an unambiguous filter
    once the per-run ticker (D8) gave way to one ticker per deal. Best effort:
    the caller treats a failure as "no seed", never as a failed run.
    """
    out: List[str] = []
    for row in _read("SELECT DISTINCT ticker FROM util_email "
                     "WHERE ticker IS NOT NULL AND ticker <> ''", ()):
        out.append(str(row.get("ticker", "")).strip().upper())
    # Runs store their whole set in one comma-separated column.
    for row in _read("SELECT DISTINCT ticker FROM util_run "
                     "WHERE ticker IS NOT NULL AND ticker <> ''", ()):
        out.extend(t.strip().upper()
                   for t in str(row.get("ticker", "")).split(","))
    return sorted({t for t in out if t})


def list_emails(util_run_id: str) -> List[Dict[str, Any]]:
    rows = _read("SELECT * FROM util_email WHERE util_run_id = ? "
                 "ORDER BY util_deal_seq, ts", (util_run_id,))
    out = []
    for d in rows:
        try:
            d["rendered_fields"] = json.loads(d["rendered_fields"]) if d.get("rendered_fields") else []
        except (TypeError, ValueError):
            d["rendered_fields"] = []
        out.append(d)
    return out


def delete_run(util_run_id: str) -> Dict[str, int]:
    """Drop a capture run and everything hanging off it."""
    deleted: Dict[str, int] = {}
    with _lock:
        conn = _connect()
        try:
            for mt in MIRROR_TABLES.values():
                cur = conn.execute(f"DELETE FROM {mt} WHERE util_run_id = ?", (util_run_id,))
                deleted[mt] = cur.rowcount
            for t in ("util_email", "util_comparison"):
                cur = conn.execute(f"DELETE FROM {t} WHERE util_run_id = ?", (util_run_id,))
                deleted[t] = cur.rowcount
            cur = conn.execute("DELETE FROM util_comparison_field WHERE util_run_id = ?",
                               (util_run_id,))
            deleted["util_comparison_field"] = cur.rowcount
            cur = conn.execute("DELETE FROM util_run WHERE util_run_id = ?", (util_run_id,))
            deleted["util_run"] = cur.rowcount
            conn.commit()
        finally:
            conn.close()
    return deleted


# ── compare passes ────────────────────────────────────────────────────────────

_FINDING_COLUMNS = ("finding_id", "comparison_id", "util_run_id", "table",
                    "column_name", "tier", "normalizer", "in_email",
                    "util_deal_seq", "util_tranche_seq", "match_confidence",
                    "expected_value", "actual_value", "verdict", "note")


def add_comparison(record: Dict[str, Any], *,
                   findings: Sequence[Dict[str, Any]] = ()) -> str:
    """Persist one compare pass and its field-level findings.

    **Idempotent per run**: comparing again replaces the previous pass and its
    findings rather than accumulating them, so fixing a `vocab_map.csv` entry and
    re-comparing improves the stored number instead of duplicating the diff.
    """
    comparison_id = str(record.get("comparison_id") or _new_id())
    run_id = record.get("util_run_id")
    rec = {
        "comparison_id": comparison_id, "util_run_id": run_id,
        "ts": record.get("ts") or _now(),
        "scores_json": json.dumps(record.get("scores") or {}, default=str),
        "notes": _coerce(record.get("notes")),
    }
    with _lock:
        conn = _connect()
        try:
            old = [r[0] for r in conn.execute(
                "SELECT comparison_id FROM util_comparison WHERE util_run_id = ?",
                (run_id,)).fetchall()]
            for prev in old:
                conn.execute("DELETE FROM util_comparison_field WHERE comparison_id = ?",
                             (prev,))
            conn.execute("DELETE FROM util_comparison WHERE util_run_id = ?", (run_id,))

            cols = list(rec.keys())
            conn.execute(
                f"INSERT INTO util_comparison ({', '.join(_quote(c) for c in cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})", [rec[c] for c in cols])

            for f in findings:
                row = {
                    "finding_id": _new_id(), "comparison_id": comparison_id,
                    "util_run_id": run_id, "table": f.get("table"),
                    "column_name": f.get("column"), "tier": f.get("tier"),
                    "normalizer": f.get("normalizer"),
                    "in_email": 1 if f.get("in_email") else 0,
                    "util_deal_seq": f.get("util_deal_seq"),
                    "util_tranche_seq": f.get("util_tranche_seq"),
                    "match_confidence": f.get("match_confidence"),
                    "expected_value": _coerce(f.get("expected_value")),
                    "actual_value": _coerce(f.get("actual_value")),
                    "verdict": f.get("verdict"), "note": f.get("note") or "",
                }
                conn.execute(
                    f"INSERT INTO util_comparison_field "
                    f"({', '.join(_quote(c) for c in _FINDING_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(_FINDING_COLUMNS))})",
                    [row[c] for c in _FINDING_COLUMNS])
            conn.commit()
        finally:
            conn.close()
    return comparison_id


def _hydrate_comparison(row: Dict[str, Any]) -> Dict[str, Any]:
    try:
        row["scores"] = json.loads(row.get("scores_json") or "") or {}
    except (TypeError, ValueError):
        row["scores"] = {}
    row.pop("scores_json", None)
    return row


def list_comparisons(util_run_id: Optional[str] = None,
                     limit: int = 100) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM util_comparison"
    args: List[Any] = []
    if util_run_id:
        sql += " WHERE util_run_id = ?"
        args.append(util_run_id)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(limit))
    return [_hydrate_comparison(r) for r in _read(sql, args)]


def get_comparison(comparison_id: str) -> Optional[Dict[str, Any]]:
    rows = _read("SELECT * FROM util_comparison WHERE comparison_id = ?", (comparison_id,))
    return _hydrate_comparison(rows[0]) if rows else None


def latest_comparison(util_run_id: str) -> Optional[Dict[str, Any]]:
    rows = list_comparisons(util_run_id, limit=1)
    return rows[0] if rows else None


def _hydrate_finding(row: Dict[str, Any]) -> Dict[str, Any]:
    row["column"] = row.pop("column_name", None)
    row["in_email"] = bool(row.get("in_email"))
    row["resolved"] = bool(row.get("resolved"))
    return row


def list_findings(*, comparison_id: Optional[str] = None,
                  util_run_id: Optional[str] = None,
                  table: Optional[str] = None,
                  verdicts: Optional[Sequence[str]] = None,
                  open_only: bool = False,
                  resolved_only: bool = False,
                  resolution_prefix: Optional[str] = None,
                  util_asset_class: Optional[str] = None,
                  limit: int = 5000) -> List[Dict[str, Any]]:
    """Field-level findings, optionally filtered — the diff and the calibration
    queue are both this query with different filters."""
    sql = ("SELECT f.*, r.util_asset_class AS util_asset_class, r.ticker AS ticker "
           "FROM util_comparison_field f "
           "LEFT JOIN util_run r ON r.util_run_id = f.util_run_id")
    where, args = [], []
    if comparison_id:
        where.append("f.comparison_id = ?")
        args.append(comparison_id)
    if util_run_id:
        where.append("f.util_run_id = ?")
        args.append(util_run_id)
    if table:
        where.append('f."table" = ?')
        args.append(table)
    if verdicts:
        where.append(f"f.verdict IN ({', '.join('?' * len(verdicts))})")
        args.extend(verdicts)
    if open_only:
        where.append("IFNULL(f.resolved, 0) = 0")
    if resolved_only:
        where.append("IFNULL(f.resolved, 0) = 1")
    if resolution_prefix:
        where.append("f.resolution LIKE ?")
        args.append(f"{resolution_prefix}%")
    if util_asset_class:
        where.append("r.util_asset_class = ?")
        args.append(util_asset_class)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += ' ORDER BY f."table", f.tier, f.column_name LIMIT ?'
    args.append(int(limit))
    return [_hydrate_finding(r) for r in _read(sql, args)]


def get_finding(finding_id: str) -> Optional[Dict[str, Any]]:
    rows = _read(
        "SELECT f.*, r.util_asset_class AS util_asset_class FROM util_comparison_field f "
        "LEFT JOIN util_run r ON r.util_run_id = f.util_run_id WHERE f.finding_id = ?",
        (finding_id,))
    return _hydrate_finding(rows[0]) if rows else None


def delete_comparison(comparison_id: str) -> int:
    """Drop one compare pass and its findings. Returns the findings removed."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM util_comparison_field WHERE comparison_id = ?",
                               (comparison_id,))
            removed = cur.rowcount
            conn.execute("DELETE FROM util_comparison WHERE comparison_id = ?", (comparison_id,))
            conn.commit()
            return removed
        finally:
            conn.close()


# ── LLM run metadata (spec 18.12) ────────────────────────────────────────────

_LLM_RUN_FIELDS = ("util_run_id", "util_email_id", "provider", "model",
                   "prompt_version", "method", "tokens_in", "tokens_out",
                   "tokens_cached", "requests", "cost_usd", "latency_ms_total",
                   "latency_ms_p50", "latency_ms_max", "started_at",
                   "finished_at", "notes")


def store_info() -> Dict[str, Any]:
    """Path, existence and per-table row counts — for the UI and smoke tests."""
    p = store_path()
    info: Dict[str, Any] = {"path": str(p), "exists": p.exists(),
                            "size_bytes": p.stat().st_size if p.exists() else 0,
                            "tables": {}}
    if not p.exists():
        return info
    with _lock:
        conn = _connect()
        try:
            names = [MIRROR_TABLES[t] for t in MIRROR_TABLES] + [
                "util_run", "util_email", "util_comparison",
                "util_comparison_field"]
            for name in names:
                try:
                    info["tables"][name] = conn.execute(
                        f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                except sqlite3.OperationalError:
                    info["tables"][name] = None
        finally:
            conn.close()
    return info
