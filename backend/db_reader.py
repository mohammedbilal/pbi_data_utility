"""Read-only fetch of the four application tables from the app's Postgres
(spec section 18.11 — "DB read safety" — and the lane rules of 18.8.3).

This is the *actual* / *baseline* half of the Email Comparison harness: the
expectation was written at email-generation time by ``expected_writer`` into the
SQLite mirror, and this module pulls what the pipeline really produced so the
two can be diffed.

Safety rails, all of them deliberate:

* **SELECT only.** There is no INSERT/UPDATE/DELETE anywhere in this file, the
  transaction is opened ``READ ONLY``, and the account in ``environments.json``
  is expected to be read-only as well. Belt, braces and a third belt — the
  utility must never be able to write to the environment under test.
* **No user SQL reaches the driver.** Table and column names come from the
  schema exports in ``reference/db_schema/`` (``expected_store.app_columns``),
  the schema/prefix identifiers are regex-validated, and every *value* —
  ticker, window bounds, issuance keys, row cap — is a bound parameter.
* **Statement timeout** (default 15s) and a **row cap** (default 5,000 per
  table, fetched as ``cap + 1`` so truncation is detected and reported).
* **Lazy ``psycopg`` import.** A missing driver or ``db.enabled = false`` is not
  an error: :func:`availability` reports why and the caller falls back to the
  CSV import route (18.11).

Nothing here interprets a value. Both sides of the comparison are text-ish and
``comparators`` absorbs the differences (``'True'`` vs ``'true'``,
``1000.00000`` vs ``1000``, epoch-ms vs ISO), so coercing types here would only
destroy information.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import comparators
from expected_store import MIRROR_TABLES, app_columns

TABLES: Tuple[str, ...] = ("issuance_deal", "issuance_data", "issuance", "issuance_security")

DEFAULT_PREFIX = "t_"
DEFAULT_SCHEMA = "public"
DEFAULT_PORT = 5432
DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_CONNECT_TIMEOUT_S = 10
DEFAULT_ROW_CAP = 5_000
LOOKBACK_MINUTES = 5          # spec 18.7: the window opens 5 min before the send
DEFAULT_WINDOW_MINUTES = 60

# Time column each table is filtered on. issuance_security has none — it is
# reached through its parent issuance_key instead (18.8.3).
TIME_COLUMN: Dict[str, Optional[str]] = {
    "issuance_deal": "deal_created_on",
    "issuance_data": "insert_time",
    "issuance": "update_time",
    "issuance_security": None,
}

# Ticker column. Same story: the security table carries no issuer.
TICKER_COLUMN: Dict[str, Optional[str]] = {
    "issuance_deal": "issuer_ticker",
    "issuance_data": "issuer_ticker",
    "issuance": "issuer_ticker",
    "issuance_security": None,
}

#: Which column names the writing source on each table. t_issuance is a
#: projection and has no datasource_id (spec 18.2); securities have none at all.
DATASOURCE_COLUMN: Dict[str, Optional[str]] = {
    "issuance_data": "datasource_id",
    "issuance": "issuance_created_by",
    "issuance_deal": "deal_id_datasource",
    "issuance_security": None,
}

#: Identifier columns each table carries **in its own right** — every one of them
#: tier 1 in the field map. This is what lets an uploaded email be matched without
#: a ticker or a window (spec 19.8): a real issuer's ticker has years of history,
#: and the agent's ingest time is unknowable from a saved file.
ISIN_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "issuance_data": ("sec_144a_isin", "sec_regs_isin"),
    "issuance": ("sec_144a_isin", "sec_regs_isin"),
    "issuance_security": ("isin",),
}
CUSIP_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "issuance_data": ("sec_144a_cusip", "sec_regs_cusip"),
    "issuance": ("sec_144a_cusip", "sec_regs_cusip"),
    "issuance_security": ("cusip",),
}

#: How a deal row is reached. `t_issuance_deal` has no `deal_id` of its own — it
#: keys on `first_deal_id`, and `t_issuance.deal_id` joins to it. Verified against
#: the live Automation database on 2026-08-10: both `first_deal_id` and
#: `master_deal_id` matched all 1,402 issuance rows, so either works and the
#: primary-looking one is used.
DEAL_KEY_COLUMN = "first_deal_id"

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_KEY_CHUNK = 500              # issuance_keys per IN (...) batch


# ── configuration ────────────────────────────────────────────────────────────

def db_settings(env: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The environment's ``db`` block with defaults filled in (spec 18.10).

    A missing block is not an error — it reads as "not configured", which the
    UI shows as a disabled Fetch button with a reason.
    """
    raw = ((env or {}).get("db") or {})
    return {
        "enabled": bool(raw.get("enabled", False)),
        "host": (raw.get("host") or "").strip(),
        "port": int(raw.get("port") or DEFAULT_PORT),
        "database": (raw.get("database") or "").strip(),
        "schema": (raw.get("schema") or DEFAULT_SCHEMA).strip(),
        "user": (raw.get("user") or "").strip(),
        "password": raw.get("password") or "",
        "sslmode": (raw.get("sslmode") or "prefer").strip(),
        "table_prefix": raw.get("table_prefix", DEFAULT_PREFIX),
        "statement_timeout_ms": int(raw.get("statement_timeout_ms") or DEFAULT_TIMEOUT_MS),
        "row_cap": int(raw.get("row_cap") or DEFAULT_ROW_CAP),
        "connect_timeout_s": int(raw.get("connect_timeout_s") or DEFAULT_CONNECT_TIMEOUT_S),
        "mirror_enabled": bool(raw.get("mirror_enabled", False)),
    }


def driver_status() -> Tuple[bool, str]:
    """(available, message). The import is deliberately deferred to call time so
    the app starts, and the CSV route keeps working, without the driver."""
    try:
        import psycopg  # noqa: F401
    except Exception as exc:                                  # pragma: no cover
        return False, (f"psycopg is not installed ({exc}). Add "
                       f"`psycopg[binary]>=3.1` (see backend/requirements.txt) "
                       f"or import the rows as CSV instead.")
    return True, "psycopg available"


def availability(env: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Can this environment be queried, and if not, why — for the Fetch button."""
    cfg = db_settings(env)
    ok, driver_msg = driver_status()
    info: Dict[str, Any] = {
        "available": False, "reason": "", "driver": ok,
        "enabled": cfg["enabled"], "host": cfg["host"], "port": cfg["port"],
        "database": cfg["database"], "schema": cfg["schema"], "user": cfg["user"],
        "target": f"{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"
                  if cfg["host"] else "",
    }
    # Configuration problems are reported before the missing driver: they are
    # what the operator has to fix either way, and a bad schema name would still
    # be a hard error the moment psycopg was installed.
    if not cfg["enabled"]:
        info["reason"] = "db.enabled is false for this environment — enable it in Settings."
    elif not cfg["host"] or not cfg["database"]:
        info["reason"] = "db.host / db.database are not configured for this environment."
    elif not _IDENT.match(cfg["schema"]):
        info["reason"] = f"db.schema {cfg['schema']!r} is not a plain identifier."
    elif cfg["table_prefix"] and not _IDENT.match(cfg["table_prefix"] + "x"):
        info["reason"] = f"db.table_prefix {cfg['table_prefix']!r} is not a plain identifier."
    elif not ok:
        info["reason"] = driver_msg
    else:
        info["available"] = True
        info["reason"] = "ready"
    return info


class DbUnavailable(RuntimeError):
    """Raised when a fetch cannot even be attempted. The caller degrades to the
    CSV import route with this message rather than surfacing a 500 (18.11)."""


# ── SQL construction (pure — no driver needed, so it is unit-testable) ────────

def _quote(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"refusing to build SQL with identifier {name!r}")
    return '"' + name + '"'


def qualified(table: str, cfg: Dict[str, Any]) -> str:
    """``"public"."t_issuance_data"`` — both parts whitelist-checked."""
    if table not in MIRROR_TABLES:
        raise KeyError(f"Unknown table {table!r}")
    return f'{_quote(cfg["schema"])}.{_quote(str(cfg["table_prefix"]) + table)}'


def split_tickers(ticker: Any) -> List[str]:
    """Normalise a run's ticker(s) to a de-duplicated, ordered list.

    Accepts what the store actually holds — one ticker, a comma-separated set
    (``util_run.ticker``, one entry per deal), or a list.
    """
    if ticker is None:
        return []
    raw = ticker if isinstance(ticker, (list, tuple, set)) else str(ticker).split(",")
    out: List[str] = []
    for t in raw:
        t = str(t).strip()
        if t and t not in out:
            out.append(t)
    return out


def build_select(table: str, cfg: Dict[str, Any], *, ticker: Any,
                 window: Optional[Tuple[datetime, datetime]] = None,
                 time_kind: Optional[str] = None,
                 row_cap: int = DEFAULT_ROW_CAP) -> Tuple[str, List[Any]]:
    """A SELECT for one table, filtered by ticker **and** the ingest window.

    Both predicates belong in SQL. Two details make that safe and fast:

    * **Tickers are matched exactly**, not through ``upper()``. A function on
      the column defeats any index — and `t_issuance_deal` has one on
      `issuer_ticker` (`index_2125_4`). The utility mints its own tickers in
      upper case and the application stores them as supplied, so an exact match
      is right; ``in_window``-style post-filtering no longer has to make up for
      it. ``ticker`` may be a single ticker or the run's whole set (a list, or
      a comma-separated string) — a run now mints one per deal, so the set is
      what scopes the run, and it is bound as an ``IN`` list.
    * **The window is bound in the column's own type.** These columns are
      ``bigint`` epoch-milliseconds in the live schema (``insert_time`` =
      ``1785975352312``), so binding a ``datetime`` would raise a type error.
      ``time_kind`` says which shape to bind — pass what
      :func:`time_column_kind` reported, so an environment where the column is a
      real ``timestamp`` still works.

    Rows whose time column is NULL are kept deliberately: the ticker already
    selected them, and dropping evidence because the app left a timestamp empty
    would silently shrink the comparison.

    Only whitelisted identifiers are interpolated; every value is bound.
    """
    cols = ", ".join(_quote(c) for c in app_columns(table))
    where: List[str] = []
    params: List[Any] = []

    tick_col = TICKER_COLUMN.get(table)
    tickers = split_tickers(ticker)
    if tickers and tick_col:
        if len(tickers) == 1:
            where.append(f"{_quote(tick_col)} = %s")
        else:
            where.append(f"{_quote(tick_col)} IN "
                         f"({', '.join(['%s'] * len(tickers))})")
        params.extend(tickers)

    time_col = TIME_COLUMN.get(table)
    if window and time_col and time_kind:
        lo, hi = _bind_window(window, time_kind)
        where.append(f"({_quote(time_col)} IS NULL OR "
                     f"{_quote(time_col)} BETWEEN %s AND %s)")
        params.extend([lo, hi])

    sql = f"SELECT {cols} FROM {qualified(table, cfg)}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if time_col:
        sql += f" ORDER BY {_quote(time_col)}"
    sql += " LIMIT %s"
    params.append(int(row_cap) + 1)              # +1 so truncation is detectable
    return sql, params


def _bind_window(window: Tuple[datetime, datetime], time_kind: str) -> Tuple[Any, Any]:
    """Window bounds in the shape the column actually holds."""
    if time_kind == "epoch_ms":
        return (int(window[0].timestamp() * 1000), int(window[1].timestamp() * 1000))
    if time_kind == "epoch_s":
        return (int(window[0].timestamp()), int(window[1].timestamp()))
    return window


def time_column_kind(cur, cfg: Dict[str, Any], table: str) -> Optional[str]:
    """Is this table's time column an epoch number or a real timestamp?

    One catalog lookup rather than an assumption: the application stores
    epoch-milliseconds in a ``bigint`` today, but a schema that used
    ``timestamptz`` would need the other binding, and guessing wrong is a query
    that fails rather than a query that is merely slow.
    """
    col = TIME_COLUMN.get(table)
    if not col:
        return None
    cur.execute("""SELECT data_type FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                      AND column_name = %s""",
                (cfg["schema"], str(cfg["table_prefix"]) + table, col))
    row = cur.fetchone()
    if not row:
        return None
    dtype = str(row["data_type"] if isinstance(row, dict) else row[0]).lower()
    if dtype in ("bigint", "numeric", "double precision", "integer", "real"):
        return "epoch_ms"
    if dtype.startswith("timestamp") or dtype == "date":
        return "timestamp"
    return None


def in_window(row: Dict[str, Any], table: str,
              window: Optional[Tuple[datetime, datetime]]) -> bool:
    """Is this row's timestamp inside the ingest window?

    Rows with no parseable timestamp are **kept** — the ticker already selected
    them, and discarding evidence because the app left a column empty (or stored
    it in a shape we did not anticipate) would silently shrink the comparison.
    """
    if not window:
        return True
    col = TIME_COLUMN.get(table)
    if not col:
        return True
    when = _as_datetime(row.get(col))
    if when is None:
        return True
    return window[0] <= when <= window[1]


def _as_datetime(value: Any) -> Optional[datetime]:
    """Epoch-ms / epoch-s / ISO / datetime -> aware UTC datetime, else None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.lstrip("+-").isdigit():
        n = int(text)
        if abs(n) >= 10 ** 12:            # milliseconds
            n /= 1000.0
        try:
            return datetime.fromtimestamp(n, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def build_security_select(cfg: Dict[str, Any], keys: Sequence[str],
                          row_cap: int) -> Tuple[str, List[Any]]:
    """Securities have no issuer and no timestamp of their own, so they are
    fetched by their parent ``issuance_key`` (18.8.3)."""
    cols = ", ".join(_quote(c) for c in app_columns("issuance_security"))
    holes = ", ".join("%s" for _ in keys)
    sql = (f"SELECT {cols} FROM {qualified('issuance_security', cfg)} "
           f"WHERE {_quote('issuance_key')} IN ({holes}) LIMIT %s")
    return sql, [*keys, int(row_cap) + 1]


def _in_clause(column: str, values: Sequence[str]) -> Tuple[str, List[Any]]:
    """``col IN (%s, …)`` — or ``col = %s`` for one value, which indexes better."""
    if len(values) == 1:
        return f"{_quote(column)} = %s", [values[0]]
    return (f"{_quote(column)} IN ({', '.join(['%s'] * len(values))})", list(values))


def build_identifier_select(table: str, cfg: Dict[str, Any], *,
                            isins: Sequence[str] = (),
                            cusips: Sequence[str] = (),
                            issuance_keys: Sequence[str] = (),
                            tranche_ids: Sequence[str] = (),
                            deal_ids: Sequence[str] = (),
                            row_cap: int = DEFAULT_ROW_CAP) -> Tuple[str, List[Any]]:
    """A SELECT for one table matched on **identifiers**, for an upload run (19.8).

    Every supplied route is OR-ed together, because no single one is complete:

    * the table's own identifier columns are populated on a *minority* of rows
      (40 of 1,402 issuance rows carry a 144A ISIN on the Automation database),
      since a pre-pricing announcement has no identifiers to state yet;
    * so ``issuance_keys`` — taken from the security rows that *did* match — is
      usually what finds the tranche;
    * ``tranche_ids`` reaches the append-only audit rows, which carry no
      ``issuance_key`` of their own;
    * ``deal_ids`` reaches the deal rollup through ``first_deal_id``.

    **No ticker and no window.** That is the whole point of this path.

    Returns ``("", [])`` when nothing was supplied to match on, so a caller can
    skip the query rather than emit an unbounded scan.
    """
    where: List[str] = []
    params: List[Any] = []

    def add(column: str, values: Sequence[str]) -> None:
        clean = sorted({str(v).strip() for v in values if str(v or "").strip()})
        if not clean:
            return
        clause, bound = _in_clause(column, clean)
        where.append(clause)
        params.extend(bound)

    if table == "issuance_deal":
        add(DEAL_KEY_COLUMN, deal_ids)
    else:
        for column in ISIN_COLUMNS.get(table, ()):
            add(column, isins)
        for column in CUSIP_COLUMNS.get(table, ()):
            add(column, cusips)
        if table in ("issuance", "issuance_security"):
            add("issuance_key", issuance_keys)
        if table == "issuance_data":
            # `tranche_id` would be the precise key, but it is **almost never
            # populated** — 31 of 1,402 issuance rows and 70 of 3,623 audit rows
            # on the Automation database (2026-08-10), so on its own it finds
            # nothing. `deal_id` does the work: 255 audit rows reachable where
            # tranche_id found 0. It is deliberately broader — every tranche of a
            # matched deal — because the comparator's row matching (18.7) narrows a
            # candidate set, and too few candidates cannot be narrowed at all.
            add("tranche_id", tranche_ids)
            add("deal_id", deal_ids)

    if not where:
        return "", []

    cols = ", ".join(_quote(c) for c in app_columns(table))
    sql = (f"SELECT {cols} FROM {qualified(table, cfg)} "
           f"WHERE {' OR '.join(where)}")
    time_col = TIME_COLUMN.get(table)
    if time_col:
        sql += f" ORDER BY {_quote(time_col)}"
    sql += " LIMIT %s"
    params.append(int(row_cap) + 1)
    return sql, params


# ── connection ───────────────────────────────────────────────────────────────

def _connect(cfg: Dict[str, Any]):
    """A read-only connection. Raises DbUnavailable with a usable message."""
    ok, msg = driver_status()
    if not ok:
        raise DbUnavailable(msg)
    import psycopg
    from psycopg.rows import dict_row
    try:
        return psycopg.connect(
            host=cfg["host"], port=cfg["port"], dbname=cfg["database"],
            user=cfg["user"], password=cfg["password"], sslmode=cfg["sslmode"],
            connect_timeout=cfg["connect_timeout_s"], autocommit=False,
            row_factory=dict_row)
    except Exception as exc:
        raise DbUnavailable(f"could not connect to "
                            f"{cfg['host']}:{cfg['port']}/{cfg['database']} — {exc}") from exc


def _begin_read_only(cur, cfg: Dict[str, Any]) -> None:
    """First statements of every transaction: no writes, bounded runtime."""
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute(f"SET LOCAL statement_timeout = {int(cfg['statement_timeout_ms'])}")


def test_connection(env: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``SELECT 1`` plus a probe that the four tables are visible."""
    info = availability(env)
    if not info["available"]:
        return {"ok": False, "reason": info["reason"], **info}
    cfg = db_settings(env)
    try:
        conn = _connect(cfg)
    except DbUnavailable as exc:
        return {"ok": False, "reason": str(exc), **info}
    missing: List[str] = []
    try:
        with conn:
            with conn.cursor() as cur:
                _begin_read_only(cur, cfg)
                cur.execute("SELECT 1 AS ok")
                cur.fetchone()
                for table in TABLES:
                    try:
                        cur.execute(f"SELECT 1 FROM {qualified(table, cfg)} LIMIT 1")
                        cur.fetchall()
                    except Exception:
                        conn.rollback()
                        with conn.cursor() as c2:
                            _begin_read_only(c2, cfg)
                        missing.append(str(cfg["table_prefix"]) + table)
    except Exception as exc:
        return {"ok": False, "reason": f"query failed — {exc}", **info}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {"ok": True, "reason": "connected",
            "missing_tables": missing, **info}


# ── lane assignment (18.8.3) ─────────────────────────────────────────────────

def resolve_window(sent_at: Optional[datetime], *,
                   window_minutes: int = DEFAULT_WINDOW_MINUTES,
                   lookback_minutes: int = LOOKBACK_MINUTES) -> Tuple[datetime, datetime]:
    """``[sent_at - 5 min, sent_at + ingest_window_minutes]`` (spec 18.7)."""
    anchor = sent_at or datetime.now(timezone.utc)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return (anchor - timedelta(minutes=max(0, lookback_minutes)),
            anchor + timedelta(minutes=max(0, int(window_minutes))))


def fetch_rows_by_identifier(env: Optional[Dict[str, Any]], *,
                             isins: Sequence[str] = (),
                             cusips: Sequence[str] = (),
                             row_cap: Optional[int] = None) -> Dict[str, Any]:
    """Pull the agent's rows for an **uploaded** email set, matched on identifiers.

    The sibling of :func:`fetch_rows`, and separate from it on purpose. A capture
    run scopes itself by minted tickers inside a 60-minute window; neither works
    for real client emails (19.8), so this path matches ISIN/CUSIP exactly and
    applies **no ticker filter, no window and no datasource filter**.

    Four steps, each narrowing from the last:

    1. security rows whose ISIN or CUSIP is one of ours;
    2. issuance rows — by those securities' ``issuance_key``, or by their own
       identifier columns;
    3. issuance_data rows — by their own identifier columns, or by the
       ``tranche_id`` of a matched issuance row (the audit table carries no
       ``issuance_key``);
    4. deal rows — by ``first_deal_id`` matching a matched issuance's ``deal_id``.

    Every existing read-only rail is unchanged: ``SELECT`` only, whitelisted
    identifiers, bound values, ``SET TRANSACTION READ ONLY``, statement timeout,
    row cap. Raises :class:`DbUnavailable` when the fetch cannot be attempted.
    """
    isin_list = sorted({str(v).strip() for v in isins if str(v or "").strip()})
    cusip_list = sorted({str(v).strip() for v in cusips if str(v or "").strip()})
    if not isin_list and not cusip_list:
        raise DbUnavailable("this upload has no ISIN or CUSIP to query on — pin an "
                            "identifier on at least one email, or import CSV exports.")
    info = availability(env)
    if not info["available"]:
        raise DbUnavailable(info["reason"])

    cfg = db_settings(env)
    cap = int(row_cap or cfg["row_cap"])
    rows: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TABLES}
    truncated: List[str] = []
    warnings: List[str] = []
    matched: Dict[str, Any] = {}

    conn = _connect(cfg)
    try:
        with conn:
            with conn.cursor() as cur:
                _begin_read_only(cur, cfg)

                def run(table: str, **kw) -> List[Dict[str, Any]]:
                    sql, params = build_identifier_select(
                        table, cfg, row_cap=cap, **kw)
                    if not sql:
                        return []
                    cur.execute(sql, params)
                    out = [dict(r) for r in cur.fetchall()]
                    if len(out) > cap:
                        truncated.append(table)
                        warnings.append(f"{table}: more than {cap} rows matched — "
                                        f"truncated. Raise db.row_cap if this is real.")
                        out = out[:cap]
                    return out

                rows["issuance_security"] = run("issuance_security",
                                                isins=isin_list, cusips=cusip_list)
                keys = sorted({str(r.get("issuance_key")) for r in rows["issuance_security"]
                               if r.get("issuance_key")})

                rows["issuance"] = run("issuance", isins=isin_list, cusips=cusip_list,
                                       issuance_keys=keys)
                tranche_ids = sorted({str(r.get("tranche_id")) for r in rows["issuance"]
                                      if r.get("tranche_id")})
                deal_ids = sorted({str(r.get("deal_id")) for r in rows["issuance"]
                                   if r.get("deal_id")})

                rows["issuance_data"] = run("issuance_data", isins=isin_list,
                                            cusips=cusip_list, tranche_ids=tranche_ids,
                                            deal_ids=deal_ids)
                rows["issuance_deal"] = run("issuance_deal", deal_ids=deal_ids)
                matched = {"issuance_keys": len(keys), "tranche_ids": len(tranche_ids),
                           "deal_ids": len(deal_ids)}
    except DbUnavailable:
        raise
    except Exception as exc:
        raise DbUnavailable(f"query failed — {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not any(rows[t] for t in TABLES):
        warnings.append(
            f"nothing in the application database carries any of these "
            f"{len(isin_list)} ISIN(s) / {len(cusip_list)} CUSIP(s). If the emails "
            f"have not been through the parsing agent in this environment yet, that "
            f"is the expected answer rather than a fault (spec 19.3).")

    return {
        "ok": True,
        "target": info["target"],
        "matched_on": "identifier",
        "isins": isin_list,
        "cusips": cusip_list,
        "matched": matched,
        "rows": rows,
        "counts": {"fetched": {t: len(rows[t]) for t in TABLES},
                   "kept": {t: len(rows[t]) for t in TABLES}},
        "truncated": truncated,
        "warnings": warnings,
    }


def fetch_rows(env: Optional[Dict[str, Any]], ticker: Any, *,
               sent_at: Optional[datetime] = None,
               last_sent_at: Optional[datetime] = None,
               window_minutes: int = DEFAULT_WINDOW_MINUTES,
               row_cap: Optional[int] = None) -> Dict[str, Any]:
    """Pull the agent's rows for one capture run's ticker(s).

    ``ticker`` is the run's whole set — one per deal, since a ticker identifies
    an issuer. Returns ``{"ok": True, "target", "ticker", "tickers", "window",
    "rows": {table: [...]}, "counts", "truncated", "warnings"}``. Raises
    :class:`DbUnavailable` when the fetch cannot be attempted — the caller turns
    that into a "use the CSV import route" message, not a 500.
    """
    tickers = split_tickers(ticker)
    if not tickers:
        raise DbUnavailable("this run has no ticker to query on — capture was "
                            "made without `unique_ticker`, so fetch by CSV import.")
    info = availability(env)
    if not info["available"]:
        raise DbUnavailable(info["reason"])

    cfg = db_settings(env)
    cap = int(row_cap or cfg["row_cap"])
    # A run can send several emails; the window opens shortly before the first
    # and closes ingest_window_minutes after the last.
    window = (resolve_window(sent_at, window_minutes=window_minutes)[0],
              resolve_window(last_sent_at or sent_at, window_minutes=window_minutes)[1])
    fetched: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TABLES}
    truncated: List[str] = []
    warnings: List[str] = []

    conn = _connect(cfg)
    try:
        with conn:
            with conn.cursor() as cur:
                _begin_read_only(cur, cfg)
                for table in ("issuance_deal", "issuance_data", "issuance"):
                    kind = time_column_kind(cur, cfg, table)
                    if kind is None and TIME_COLUMN.get(table):
                        warnings.append(
                            f"{table}: could not tell what type "
                            f"{TIME_COLUMN[table]} is, so the window is applied "
                            f"after the fetch rather than in the query.")
                    sql, params = build_select(table, cfg, ticker=tickers,
                                               window=window, time_kind=kind,
                                               row_cap=cap)
                    cur.execute(sql, params)
                    rows = [dict(r) for r in cur.fetchall()]
                    if len(rows) > cap:
                        truncated.append(table)
                        rows = rows[:cap]
                        warnings.append(
                            f"{table}: more than {cap} rows matched the run's "
                            f"ticker(s) — the result is truncated. Raise "
                            f"db.row_cap if this is real.")
                    fetched[table] = rows

                # Securities have no timestamp of their own, so they are reached
                # through their parent issuance — and only through the parents
                # that survived the window, or a ticker with months of history
                # would drag in every security it ever had.
                parents = [r for r in fetched["issuance"]
                           if in_window(r, "issuance", window)]
                keys = sorted({str(r.get("issuance_key")) for r in parents
                               if r.get("issuance_key")})
                sec: List[Dict[str, Any]] = []
                for i in range(0, len(keys), _KEY_CHUNK):
                    sql, params = build_security_select(cfg, keys[i:i + _KEY_CHUNK], cap)
                    cur.execute(sql, params)
                    sec.extend(dict(r) for r in cur.fetchall())
                if len(sec) > cap:
                    truncated.append("issuance_security")
                    sec = sec[:cap]
                fetched["issuance_security"] = sec
                if not keys and parents:
                    warnings.append("issuance rows carry no issuance_key, so no "
                                    "security rows could be reached.")
    except DbUnavailable:
        raise
    except Exception as exc:
        raise DbUnavailable(f"query failed — {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # A safety net only: the window is in the WHERE clause now, so this drops
    # nothing unless the column type could not be determined.
    #
    # There is deliberately NO datasource filter. It was a trap: nothing in the
    # live application stamps the value the config expected, so every row that
    # HAS a datasource column was silently discarded while `t_issuance_security`
    # — which has none — came through, producing a comparison built entirely of
    # security rows and an accuracy figure that measured nothing. The run's set
    # of per-deal tickers (D8, revised) already isolates a run's rows, so the
    # filter bought nothing. What the application stamped is far more useful as a compared
    # field in the diff than as a reason to throw the row away.
    rows: Dict[str, List[Dict[str, Any]]] = {}
    dropped_window = 0
    for table in TABLES:
        keep = []
        for row in fetched[table]:
            if not in_window(row, table, window):
                dropped_window += 1
                continue
            keep.append(row)
        rows[table] = keep

    if dropped_window:
        # Normally zero — the window is in the WHERE clause. Anything here came
        # back because the column type could not be determined.
        warnings.append(f"{dropped_window} row(s) matched the run's ticker(s) "
                        f"but fell outside the {window_minutes}-minute window "
                        f"— ignored.")

    return {
        "ok": True,
        "target": info["target"],
        "ticker": ", ".join(t.upper() for t in tickers),
        "tickers": [t.upper() for t in tickers],
        "window": {"from": window[0].isoformat(), "to": window[1].isoformat(),
                   "minutes": int(window_minutes)},
        "rows": rows,
        "counts": {"fetched": {t: len(fetched[t]) for t in TABLES},
                   "kept": {t: len(rows[t]) for t in TABLES}},
        "truncated": truncated,
        "warnings": warnings,
    }
