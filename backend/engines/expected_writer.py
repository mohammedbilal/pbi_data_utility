"""Expected-state projection — turns generated bond payloads + email metadata
into the rows the application *should* end up holding (spec section 18.5).

This module is **pure**: it performs no network calls, opens no database and
writes nothing. It reads the field/vocab maps once (through
``expected_store.effective_map``, so calibration overrides apply) and otherwise
just transforms dicts, exactly like ``email_builder.py``. Persistence is
``expected_store.insert_rows``; the engine wiring lives in ``bonds_engine``.

What it produces, per email (= per deal):

    1 x issuance_deal      per deal
    1 x issuance_data      per tranche     (the audit row)
    1 x issuance           per tranche     (projection of that audit row)
    1 x issuance_security  per non-empty identifier set on the tranche
                                           (so a 144A/Reg S tranche -> 2, D2)

Two rules matter more than the plumbing:

* **The map decides which columns exist.** Only ``(table, column)`` pairs in
  ``field_map.csv`` whose ``source_kind`` is one of
  ``payload / derived / const / pool / email_meta`` are projected. Everything
  else (``system`` / ``null``) is left NULL and is not scored. Adding a column
  to the CSV is enough for a straight ``payload`` copy; anything derived needs
  a resolver in ``_RESOLVERS`` below, and a missing one is reported as a
  warning rather than silently dropped.
* **The expectation carries the normalized value, never the rendered prose**
  (spec 18.6.1). A ``vocab`` normalizer is applied here, so
  ``use_of_proceeds`` holds ``GENERAL CORPORATE PURPOSES`` and not the
  150-character sentence the email printed, and ``rating_outlook`` holds the
  outlook split off the ``Baa2/Stable`` string the template glued together.

Values that only the *email* knows (the gap-fill pools: use of proceeds, the
``T+n`` settlement label, the optional-redemption line, the rating outlook)
arrive in ``email_meta["gap_fill"]``, which ``email_builder.build_email``
returns alongside the subject and body.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from expected_store import effective_map

# Table keys are the ``expected_store.MIRROR_TABLES`` keys (no t_ / util_ prefix).
TABLES: Tuple[str, ...] = ("issuance_deal", "issuance_data", "issuance", "issuance_security")

# Column sources this module knows how to project. `system` (app-assigned) and
# `null` (no source) stay NULL — see spec 18.5.
PROJECTED_KINDS = frozenset({"payload", "derived", "const", "pool", "email_meta"})

# `source` for a payload column names the DETAILS key, sometimes with prose
# around it ("tranche-1 ISSUER_NAME", "IS_ROADSHOW (false)").
_PAYLOAD_KEY = re.compile(r"[A-Z][A-Z0-9_]{2,}")
_TENOR = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*Y(?:EARS?|R)?\s*$", re.I)
_SPLIT = re.compile(r"[,/;]")

_MISSING = object()          # "no resolver / not projectable" — distinct from NULL


# ── helpers ──────────────────────────────────────────────────────────────────

def _payload_key(source: str) -> Optional[str]:
    m = _PAYLOAD_KEY.search(source or "")
    return m.group(0) if m else None


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return value


def _as_bool(value: Any) -> Optional[bool]:
    if value is None or isinstance(value, bool):
        return value
    s = str(value).strip().casefold()
    if s in ("true", "t", "1", "y", "yes"):
        return True
    if s in ("false", "f", "0", "n", "no"):
        return False
    return None


def _tenor_years(det: Dict[str, Any]) -> Optional[float]:
    m = _TENOR.match(str(det.get("PRELIMINARY_SECURITY_TENOR") or ""))
    if not m:
        return None
    n = float(m.group(1))
    return int(n) if n == int(n) else n


def _split_set(value: Any) -> List[str]:
    """'Barclays/BNP Paribas' -> ['Barclays', 'BNP Paribas'] (order preserved)."""
    return [p.strip() for p in _SPLIT.split(str(value or "")) if p.strip()]


def _by_tranche(values: Sequence[Any], i: int) -> Any:
    """A gap-fill pool pick for tranche ``i``.

    Formats that render the pool once per deal (stacked, inline) produce a
    single pick that applies to every tranche; formats that render it per
    tranche (the tranche table, bond_single) produce one per tranche.
    """
    if not values:
        return None
    return values[i] if i < len(values) else values[0]


# ── context ──────────────────────────────────────────────────────────────────

class _Ctx:
    """Everything a resolver needs: the deal's tranches + the email metadata."""

    def __init__(self, dets: List[Dict[str, Any]], email_meta: Dict[str, Any],
                 maps: Dict[str, Any]) -> None:
        self.dets = dets
        self.meta = email_meta or {}
        self.gap: Dict[str, Any] = self.meta.get("gap_fill") or {}
        self.fields: Dict[Tuple[str, str], Dict[str, str]] = maps["fields"]
        self.vocab: Dict[Tuple[str, str], Dict[str, str]] = maps["vocab"]
        self.tranche_count = len(dets)
        # Blank means "we make no claim about what the app stamps" — the three
        # datasource columns are then left NULL and whatever the application
        # wrote shows up in the diff as `extra`, which is honest. Guessing a
        # value would report a mismatch on every row for no reason: nothing in
        # the live database carries the value this once defaulted to.
        self.datasource = _clean(self.meta.get("expected_datasource")) or None
        self.warnings: List[str] = []

    # deal-level columns read tranche 1 (spec 18.5.3)
    def det(self, table: str, i: int) -> Dict[str, Any]:
        return self.dets[0] if table == "issuance_deal" else self.dets[i]

    def announcement(self) -> Optional[str]:
        sent = self.meta.get("sent_at")
        if isinstance(sent, datetime):
            dt = sent if sent.tzinfo else sent.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        if isinstance(sent, str) and sent:
            return sent[:10]
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def vocab_value(self, column: str, generated: Any) -> Any:
        """Generated text -> the value the app stores (spec 18.6.1).

        An unmapped pair keeps the generated value and is reported, so the
        first live run fills the map through the calibration loop instead of
        the expectation quietly claiming the prose is what the DB holds.
        """
        if generated is None:
            return None
        entry = self.vocab.get((column, str(generated).strip().casefold()))
        if entry and entry.get("db_value"):
            return entry["db_value"]
        text = str(generated)
        self.warnings.append(
            f"vocab_map has no entry for {column} = {text[:60]!r} — expectation "
            f"keeps the generated value; calibrate after the first live run")
        return generated


# ── resolvers for everything that isn't a straight payload copy ──────────────
# Keyed by (table, column) first, then by column name (most are shared between
# issuance_data and issuance, which hold the same value by definition).

def _r_datasource(ctx: _Ctx, table: str, i: int) -> Any:
    return ctx.datasource


def _r_tranche_count(ctx: _Ctx, table: str, i: int) -> Any:
    return ctx.tranche_count


def _r_announcement(ctx: _Ctx, table: str, i: int) -> Any:
    return ctx.announcement()


def _r_deal_bookrunners(ctx: _Ctx, table: str, i: int) -> Any:
    seen: Dict[str, str] = {}
    for det in ctx.dets:
        for name in _split_set(det.get("BOOKRUNNERS")):
            seen.setdefault(name.casefold(), name)
    return ", ".join(seen.values()) or None


def _r_deal_currencies(ctx: _Ctx, table: str, i: int) -> Any:
    seen: Dict[str, str] = {}
    for det in ctx.dets:
        c = _clean(det.get("CURRENCY_CODE"))
        if c:
            seen.setdefault(str(c).casefold(), str(c))
    return ", ".join(seen.values()) or None


def _r_is_reg_s(ctx: _Ctx, table: str, i: int) -> Any:
    return "reg s" in str(ctx.det(table, i).get("REGISTRATION_TYPE") or "").casefold()


def _r_is_144a(ctx: _Ctx, table: str, i: int) -> Any:
    return "144a" in str(ctx.det(table, i).get("REGISTRATION_TYPE") or "").casefold()


def _r_is_unsecured(ctx: _Ctx, table: str, i: int) -> Any:
    return "unsecured" in str(ctx.det(table, i).get("BOND_SENIORITY") or "").casefold()


def _r_tenor(ctx: _Ctx, table: str, i: int) -> Any:
    years = _tenor_years(ctx.det(table, i))
    return None if years is None else f"{years} Y"


def _r_years_to_maturity(ctx: _Ctx, table: str, i: int) -> Any:
    return _tenor_years(ctx.det(table, i))


def _r_rating_classification(ctx: _Ctx, table: str, i: int) -> Any:
    # normalizer = vocab, so the map turns 'Investment Grade' into 'IG'
    return _clean(ctx.det(table, i).get("BOND_CLASS"))


def _r_benchmark(ctx: _Ctx, table: str, i: int) -> Any:
    """The float benchmark inside IPTS ('SOFR + 120bps' -> 'SOFR'); NULL on fixed."""
    det = ctx.det(table, i)
    if str(det.get("COUPON_TYPE") or "").strip().casefold() == "fixed":
        return None
    ipts = str(det.get("IPTS") or "")
    head = ipts.split("+", 1)[0].strip()
    return head or None


def _r_rating_outlook(ctx: _Ctx, table: str, i: int) -> Any:
    """The outlook the template glues onto each rating — split out per 18.6.1.

    NULL when the chosen format never printed one (bond_colon), because then
    there is nothing for the agent to extract.
    """
    return _clean(ctx.gap.get("rating_outlook"))


def _r_use_of_proceeds(ctx: _Ctx, table: str, i: int) -> Any:
    # normalizer = vocab: the prose is classified into the DB code here.
    return _by_tranche(ctx.gap.get("uop_bond") or [], 0 if table == "issuance_deal" else i)


def _r_settlement_period(ctx: _Ctx, table: str, i: int) -> Any:
    """The app keeps the email's ``T+n`` label verbatim (values seen live include
    both ``T+2`` and bare ``2``, and the LLM-ingested rows carry ``T+2``), so the
    expectation keeps the label rather than the bare number."""
    n = _by_tranche(ctx.gap.get("settlement_period") or [], i)
    if n is None:
        return None
    text = str(n).strip()
    return text if text.upper().startswith("T+") else f"T+{text}"


def _r_call_indicator(ctx: _Ctx, table: str, i: int) -> Any:
    """The Optional Redemption line implies a call; NULL when it wasn't rendered."""
    value = _by_tranche(ctx.gap.get("optional_redemption") or [], i)
    return None if value is None else ("call" in str(value).casefold())


def _r_put_indicator(ctx: _Ctx, table: str, i: int) -> Any:
    return False


def _r_business_description(ctx: _Ctx, table: str, i: int) -> Any:
    return _by_tranche(ctx.gap.get("business_bond") or [], i)


def _const(value: Any):
    def _fn(ctx: _Ctx, table: str, i: int) -> Any:
        return value
    return _fn


_RESOLVERS: Dict[Any, Any] = {
    # lane discriminators (spec 18.5.2 / D1) — three differently-named columns,
    # one value: compare.expected_datasource.
    "datasource_id": _r_datasource,
    "deal_id_datasource": _r_datasource,
    "issuance_created_by": _r_datasource,

    # deal-level rollups
    ("issuance_deal", "bookrunners"): _r_deal_bookrunners,
    ("issuance_deal", "deal_currencies"): _r_deal_currencies,
    ("issuance_deal", "total_issuances"): _r_tranche_count,
    ("issuance_deal", "pbi_deal_status"): _const("OPEN"),
    ("issuance_deal", "investor_status"): _const("PENDING"),
    ("issuance_deal", "blast_status"): _const(False),
    ("issuance_deal", "priced"): _const(False),

    "db_number_of_tranches": _r_tranche_count,
    "announcement_dt": _r_announcement,

    # per-tranche derivations
    "is_reg_s": _r_is_reg_s,
    "is_144a": _r_is_144a,
    "is_unsecured": _r_is_unsecured,
    "tenor": _r_tenor,
    "years_to_maturity": _r_years_to_maturity,
    "rating_classification": _r_rating_classification,
    "coupon_index": _r_benchmark,
    "benchmark_for_pricing": _r_benchmark,
    "rating_outlook": _r_rating_outlook,
    "use_of_proceeds": _r_use_of_proceeds,
    "tranche_settlement_period": _r_settlement_period,
    "call_indicator": _r_call_indicator,
    "put_indicator": _r_put_indicator,
    "tranche_issuer_business_description": _r_business_description,

    # constants the app stamps on a first announcement
    "book_status": _const("Book Open"),
    "tranche_state": _const("ANNOUNCED"),
    "issuance_bond_type": _const("ISSUANCE"),
    "security_status": _const("ACTIVE"),
    "security_type": _const("NOT_SPECIFIED"),
}


# ── projection ───────────────────────────────────────────────────────────────

def _columns(ctx: _Ctx, table: str) -> List[Tuple[str, Dict[str, str]]]:
    return [(col, entry) for (t, col), entry in ctx.fields.items()
            if t == table and entry.get("source_kind") in PROJECTED_KINDS]


def _value(ctx: _Ctx, table: str, column: str, entry: Dict[str, str], i: int) -> Any:
    kind = entry.get("source_kind")
    if kind == "payload":
        key = _payload_key(entry.get("source", ""))
        if not key:
            ctx.warnings.append(f"{table}.{column}: payload source "
                                f"{entry.get('source')!r} names no DETAILS key")
            return _MISSING
        raw = ctx.det(table, i).get(key)
    else:
        fn = _RESOLVERS.get((table, column)) or _RESOLVERS.get(column)
        if fn is None:
            ctx.warnings.append(f"{table}.{column}: no resolver for source_kind "
                                f"'{kind}' — expectation left NULL")
            return _MISSING
        raw = fn(ctx, table, i)

    value = _clean(raw)
    if value is None:
        return None

    normalizer = entry.get("normalizer")
    if normalizer == "vocab":
        return ctx.vocab_value(column, value)
    if normalizer == "bool":
        return _as_bool(value)
    return value


def _row(ctx: _Ctx, table: str, i: int) -> Dict[str, Any]:
    """One mirror row: every projectable column of ``table`` for tranche ``i``.

    Columns whose value resolves to NULL are omitted rather than written as an
    explicit NULL — the store leaves absent columns NULL either way, and an
    omitted key keeps the row readable in the UI.
    """
    row: Dict[str, Any] = {}
    for column, entry in _columns(ctx, table):
        value = _value(ctx, table, column, entry, i)
        if value is _MISSING or value is None:
            continue
        row[column] = value
    return row


def _security_rows(ctx: _Ctx, i: int) -> List[Dict[str, Any]]:
    """One row per non-empty identifier set (D2: 144A/Reg S expects two)."""
    det = ctx.dets[i]
    mapped = {col for col, _ in _columns(ctx, "issuance_security")}
    out: List[Dict[str, Any]] = []
    for flavour, prefix in (("Reg S", "SEC_REGS"), ("144A", "SEC_144A")):
        ids = {name: _clean(det.get(f"{prefix}_{name.upper()}"))
               for name in ("isin", "cusip", "figi")}
        if not any(ids.values()):
            continue
        row: Dict[str, Any] = {k: v for k, v in ids.items() if k in mapped and v is not None}
        if "registration_type" in mapped:
            row["registration_type"] = ctx.vocab_value("registration_type", flavour)
        if "security_status" in mapped:
            row["security_status"] = "ACTIVE"
        if "security_type" in mapped:
            # The app names the security by its registration flavour — `REGS`,
            # `144A`, `SEC_REGISTERED` are the values in pbi.t_issuance_security.
            # (An earlier guess of NOT_SPECIFIED came from one sample export.)
            row["security_type"] = "REGS" if flavour == "Reg S" else "144A"
        row["util_match_key"] = ids["isin"] or ids["cusip"] or f"{flavour}#{i + 1}"
        out.append(row)
    return out


def _match_key(det: Dict[str, Any]) -> str:
    """Spec 18.7 key 2 — ticker + currency + tenor, unambiguous once the run
    mints its own ticker (D8)."""
    return "|".join(str(det.get(k) or "").strip().upper() for k in
                    ("ISSUER_TICKER", "CURRENCY_CODE", "PRELIMINARY_SECURITY_TENOR"))


# ── the label path (spec 19.6, build step B1) ────────────────────────────────
# An uploaded email has no payload behind it, so ground truth is stated by a
# human instead (D10). This path takes those stated values **directly** and runs
# no resolver: there is nothing to derive from, and inventing a value would be
# exactly the guess the labelling form exists to avoid.

#: A label's identifier sets, in the order `_security_rows` uses.
_LABEL_FLAVOURS = ("144A", "Reg S")


def _label_columns(fields: Dict[Tuple[str, str], Dict[str, str]],
                   table: str, stated: Dict[str, Any],
                   vocab: Dict[Tuple[str, str], Dict[str, str]],
                   warnings: List[str],
                   filled: set) -> Dict[str, Any]:
    """Stated values projected onto one table, skipping what it does not have.

    ``t_issuance`` is a projection and lacks seven columns ``t_issuance_data``
    carries (18.2), so the same label writes a different column set to each — the
    field map decides, not the caller.
    """
    row: Dict[str, Any] = {}
    for column, raw in (stated or {}).items():
        entry = fields.get((table, column))
        if entry is None:
            continue                      # not a column of this table; not an error
        value = _clean(raw)
        if value is None or value == "":
            continue                      # blank means "the email did not say it" (D12)
        normalizer = (entry.get("normalizer") or "").strip()
        if normalizer == "vocab":
            key = (column, str(value).strip().casefold())
            mapped = vocab.get(key)
            if mapped and mapped.get("db_value"):
                value = mapped["db_value"]
            else:
                warnings.append(
                    f"{column} = {str(value)[:50]!r} is not in vocab_map.csv — kept "
                    f"as stated. Add it if the application spells it differently.")
        elif normalizer == "bool":
            value = _as_bool(value)
            if value is None:
                continue
        row[column] = value
        filled.add(column)
    return row


def from_label(label: Dict[str, Any], *, asset_class: str = "bonds",
               maps: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Project one uploaded email's **label** into expected mirror rows (19.6).

    Pure — no store, no I/O, no clock. The sibling of :func:`build_expected`, and
    separate from it because the two have nothing in common but their output shape:
    that one derives values from a payload it generated, this one is told them.

    The label is::

        {"deals": [{"deal": {<column>: <value>, ...},
                    "tranches": [{"columns": {...},
                                  "securities": [{"flavour": "144A",
                                                  "isin": ..., "cusip": ...,
                                                  "figi": ...}]}]}]}

    Several deals per email is normal, not an edge case: one real sample states two
    different legal issuers under one ticker with different formats and rankings
    (19.17), and how many deals an email describes is a labelling input rather than
    something the utility can infer.

    Returns ``{"rows", "warnings", "counts", "rendered_fields"}``.
    **``rendered_fields`` is the set of columns the label actually filled**, which
    is what the accuracy denominator is measured over (19.7) — a column left blank
    is excluded from scoring rather than counted as a miss.
    """
    resolved = maps or effective_map(asset_class)
    fields: Dict[Tuple[str, str], Dict[str, str]] = resolved["fields"]
    vocab: Dict[Tuple[str, str], Dict[str, str]] = resolved["vocab"]

    rows: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TABLES}
    warnings: List[str] = []
    filled: set = set()
    tranche_total = 0

    deals = label.get("deals") or []
    if not deals:
        return {"rows": rows, "warnings": ["the label states no deals"],
                "counts": {"deals": 0, "tranches": 0, "securities": 0},
                "rendered_fields": []}

    known_columns = {col for (_t, col) in fields}
    for stated_key in _stated_column_names(deals):
        if stated_key not in known_columns:
            warnings.append(f"{stated_key!r} is not a column of any of the four "
                            f"tables — ignored.")

    for deal_index, deal_label in enumerate(deals, start=1):
        stated_deal = deal_label.get("deal") or {}
        tranches = deal_label.get("tranches") or []

        deal_row = _label_columns(fields, "issuance_deal", stated_deal, vocab,
                                  warnings, filled)
        # Two deal values are stated by the label's own **shape** rather than by the
        # labeller, so they need no resolver and cannot be got wrong: how many
        # tranches there are, and which currencies they are in. Both are tier 1, and
        # asking someone to retype what they have already entered per tranche is how
        # a form earns a reputation for being tedious.
        if ("issuance_deal", "db_number_of_tranches") in fields and tranches:
            deal_row.setdefault("db_number_of_tranches", len(tranches))
        if ("issuance_deal", "deal_currencies") in fields and tranches:
            currencies: List[str] = []
            for tranche in tranches:
                stated_columns = tranche.get("columns") or {}
                value = _clean(stated_columns.get("currency_code")
                               or stated_columns.get("tranche_currency"))
                if value and str(value) not in currencies:
                    currencies.append(str(value))
            if currencies:
                deal_row.setdefault("deal_currencies", ",".join(currencies))
        deal_row["util_deal_seq"] = deal_index
        deal_row["util_match_key"] = str(
            stated_deal.get("issuer_ticker") or "").strip().upper()
        rows["issuance_deal"].append(deal_row)

        for tranche_index, tranche in enumerate(tranches, start=1):
            stated = dict(tranche.get("columns") or {})
            tranche_total += 1
            # Deal-grain values repeat onto the tranche rows where the tranche
            # tables carry the same column, which is how a capture run writes them.
            merged = {**{k: v for k, v in stated_deal.items()
                         if ("issuance_data", k) in fields}, **stated}
            key = "|".join(str(merged.get(c) or "").strip().upper() for c in
                           ("issuer_ticker", "currency_code",
                            "preliminary_security_tenor"))
            for table in ("issuance_data", "issuance"):
                row = _label_columns(fields, table, merged, vocab, warnings, filled)
                if not row:
                    continue
                row["util_deal_seq"] = deal_index
                row["util_tranche_seq"] = tranche_index
                row["util_match_key"] = key
                rows[table].append(row)

            for sec in _label_security_rows(fields, tranche, vocab, warnings, filled):
                sec["util_deal_seq"] = deal_index
                sec["util_tranche_seq"] = tranche_index
                rows["issuance_security"].append(sec)

    seen: Dict[str, None] = {}
    for w in warnings:
        seen.setdefault(w, None)

    return {
        "rows": rows,
        "warnings": list(seen),
        "counts": {"deals": len(deals), "tranches": tranche_total,
                   "securities": len(rows["issuance_security"])},
        "rendered_fields": sorted(filled),
    }


def _stated_column_names(deals: Sequence[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for deal in deals:
        names.extend((deal.get("deal") or {}).keys())
        for tranche in (deal.get("tranches") or []):
            names.extend((tranche.get("columns") or {}).keys())
    return sorted(set(names))


def _label_security_rows(fields: Dict[Tuple[str, str], Dict[str, str]],
                         tranche: Dict[str, Any],
                         vocab: Dict[Tuple[str, str], Dict[str, str]],
                         warnings: List[str], filled: set) -> List[Dict[str, Any]]:
    """One row per identifier set the label states — two for 144A/Reg S (D2).

    Confirmed against the real application on 2026-08-10: a dual-set tranche is
    stored as two rows, ``144A``/``144A`` and ``Reg S``/``REGS``, and the Reg S leg
    may carry a CUSIP with no ISIN (19.19.4).
    """
    out: List[Dict[str, Any]] = []
    mapped = {col for (t, col) in fields if t == "issuance_security"}
    for spec in (tranche.get("securities") or []):
        ids = {name: _clean(spec.get(name)) for name in ("isin", "cusip", "figi")}
        if not any(ids.values()):
            continue
        flavour = str(spec.get("flavour") or "").strip() or "144A"
        row: Dict[str, Any] = {k: v for k, v in ids.items()
                               if k in mapped and v is not None}
        filled.update(k for k in row)
        extra = _label_columns(fields, "issuance_security",
                               {k: v for k, v in spec.items()
                                if k not in ("isin", "cusip", "figi", "flavour")},
                               vocab, warnings, filled)
        row.update(extra)
        if "registration_type" in mapped and "registration_type" not in row:
            # The flavour *is* the security's registration type — identity at both
            # grains (19.19.4), so this is a copy rather than a translation.
            row["registration_type"] = flavour
            filled.add("registration_type")
        if "security_type" in mapped and "security_type" not in row:
            # `Reg S` -> `REGS`, anything else -> `144A`, matching the two values
            # the application actually stores.
            is_regs = flavour.replace(" ", "").replace("-", "").casefold() == "regs"
            row["security_type"] = "REGS" if is_regs else "144A"
        row["util_match_key"] = ids["isin"] or ids["cusip"] or flavour
        out.append(row)
    return out


def build_expected(payloads: Sequence[Dict[str, Any]],
                   email_meta: Optional[Dict[str, Any]] = None,
                   *, asset_class: str = "bonds", deal_seq: int = 1,
                   maps: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Project one deal's payloads into expected mirror rows.

    ``payloads`` are the full generated payloads for a single deal (the same
    list handed to ``email_builder.build_email``); ``email_meta`` is that
    builder's third return value plus ``sent_at`` and
    ``expected_datasource``.

    Returns ``{"rows": {table: [row, ...]}, "warnings": [...],
    "counts": {"deals": 1, "tranches": n, "securities": m}}``. Rows carry app
    column names plus ``util_deal_seq`` / ``util_tranche_seq`` /
    ``util_match_key``, ready for ``expected_store.insert_rows``.
    """
    dets = [p.get("DETAILS", {}) or {} for p in payloads]
    if not dets:
        return {"rows": {t: [] for t in TABLES}, "warnings": [],
                "counts": {"deals": 0, "tranches": 0, "securities": 0}}

    ctx = _Ctx(dets, email_meta or {}, maps or effective_map(asset_class))
    rows: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TABLES}

    deal = _row(ctx, "issuance_deal", 0)
    deal["util_deal_seq"] = deal_seq
    deal["util_match_key"] = str(dets[0].get("ISSUER_TICKER") or "").strip().upper()
    rows["issuance_deal"].append(deal)

    for i, det in enumerate(dets):
        key = _match_key(det)
        for table in ("issuance_data", "issuance"):
            row = _row(ctx, table, i)
            row["util_deal_seq"] = deal_seq
            row["util_tranche_seq"] = i + 1
            row["util_match_key"] = key
            rows[table].append(row)
        for sec in _security_rows(ctx, i):
            sec["util_deal_seq"] = deal_seq
            sec["util_tranche_seq"] = i + 1
            rows["issuance_security"].append(sec)

    # One warning per distinct problem — a 10-tranche deal would otherwise
    # repeat the same unmapped-vocab line ten times.
    seen: Dict[str, None] = {}
    for w in ctx.warnings:
        seen.setdefault(w, None)

    return {
        "rows": rows,
        "warnings": list(seen),
        "counts": {"deals": 1, "tranches": len(dets),
                   "securities": len(rows["issuance_security"])},
    }
