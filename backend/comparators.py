"""Comparison engine for the Email Comparison harness (spec section 18.7-18.9).

Given the *expected* rows the utility captured when it generated an email and
the *actual* rows the parsing agent wrote into the application database, this
module answers one question: which fields did the agent get right?

A value the agent parsed into the application's own vocabulary counts as right
-- 'Semi annual' in the email against 'Semi-Annual' in the database is a
success, not a failure. That is what the normalizers below are for.

This module is **pure**. It opens no database, makes no network call and writes
nothing: rows arrive as dicts of app column names, the field/vocab maps arrive
as ``expected_store.effective_map()`` output (injectable, so a caller comparing
many row pairs loads them once), and everything else is arithmetic. Persistence
and fetching belong to ``routers/compare_router.py`` and ``db_reader.py``.

Three things happen here, in order:

1. **Normalisation** (18.8). Every scored column carries a normalizer in
   ``field_map.csv``. Values arrive as TEXT on both sides — SQLite stores
   everything as text, and a Postgres fetch hands back ``'True'`` where the
   store holds ``'true'`` and ``'1000.00000'`` where it holds ``'1000'`` — so
   every normalizer copes with either shape.
2. **Row matching** (18.7). App-assigned ids are useless across lanes, so rows
   are paired on business keys in priority order, each carrying a match
   confidence, with unmatched rows reported separately (they are the difference
   between recall and precision).
3. **Verdicts and scoring** (18.8). Four outcomes -- match, mismatch, missing,
   extra -- and one accuracy figure, measured only over the fields the email
   actually rendered.

"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

TABLES: Tuple[str, ...] = ("issuance_deal", "issuance_data", "issuance", "issuance_security")

# ── verdicts (spec 18.8 / 18.8.1) ─────────────────────────────────────────────

MATCH = "match"
MISMATCH = "mismatch"
MISSING = "missing"
EXTRA = "extra"
NOT_COMPARED = "not_compared"

VERDICTS: Tuple[str, ...] = (MATCH, MISMATCH, MISSING, EXTRA, NOT_COMPARED)

#: Verdicts that count as the agent getting the field right / wrong. EXTRA and
#: NOT_COMPARED are reported but never scored.
SCORED_RIGHT = frozenset({MATCH})
SCORED_WRONG = frozenset({MISMATCH, MISSING})
UNSCORED = frozenset({EXTRA, NOT_COMPARED})

DEFAULT_CONFIG: Dict[str, Any] = {
    "date_tolerance_days": {"default": 0, "maturity_date": 3},
    "expected_datasource": "LLM",
    "ingest_window_minutes": 60,
}

# Utility metadata rides alongside the app columns and is never compared.
_META_PREFIX = "util_"

_NULL_TOKENS = frozenset({"", "null", "none", "nan", "\\n"})

_WS = re.compile(r"\s+")
_SLASH_WS = re.compile(r"\s*/\s*")
_LIST_SPLIT = re.compile(r"[,/;]")
_PARENS = re.compile(r"\s*\([^)]*\)\s*")
_NUM = re.compile(r"[-+]?\d[\d,_]*(?:\.\d+)?")
_AMOUNT = re.compile(
    r"^\s*(?:[^\d\-+.]*?)([-+]?\d[\d,_]*(?:\.\d+)?)\s*"
    r"(k|m|mm|mn|bn|bln|b|tn|trn|t)?\b", re.I)
_TENOR = re.compile(
    r"^\s*([-+]?\d+(?:\.\d+)?)\s*"
    r"(y|yr|yrs|year|years|m|mo|mos|month|months)?\.?\s*$", re.I)
_DMY = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$")
_YMD = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$")
_EPOCH = re.compile(r"^\s*[-+]?\d{9,14}\s*$")

_AMOUNT_MULTIPLIER: Dict[str, Decimal] = {
    "k": Decimal(10) ** 3,
    "m": Decimal(10) ** 6, "mm": Decimal(10) ** 6, "mn": Decimal(10) ** 6,
    "b": Decimal(10) ** 9, "bn": Decimal(10) ** 9, "bln": Decimal(10) ** 9,
    "t": Decimal(10) ** 12, "tn": Decimal(10) ** 12, "trn": Decimal(10) ** 12,
}

_TRUE = frozenset({"true", "t", "1", "y", "yes", "on"})
_FALSE = frozenset({"false", "f", "0", "n", "no", "off"})

_NUMERIC_EPSILON = Decimal("0.000001")     # spec 18.8: 1e-6


# ── normalisation ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Canon:
    """A value reduced to something comparable, plus how it got there.

    ``value`` is the canonical form (``Decimal`` / ``date`` / ``bool`` /
    ``frozenset`` / ``tuple`` / ``str``) or ``None`` for a null. ``text`` keeps
    the original for display. ``unmapped`` marks a ``vocab`` value with no
    ``vocab_map.csv`` entry — reported by name at compare time (18.8) so the map
    fills in from real runs rather than guesswork.
    """
    kind: str
    value: Any
    text: Optional[str]
    null: bool = False
    unmapped: bool = False


def is_null(value: Any) -> bool:
    """True for SQL NULL and the strings that stand in for it.

    The CSV import route writes ``NULL`` for nulls (18.11) and an empty cell is
    indistinguishable from an empty string in a fetched row, so both read as
    null here. A real value of ``'NULL'`` does not occur in these tables.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in _NULL_TOKENS
    return False


def _text(value: Any) -> str:
    """Trim + collapse internal whitespace. Also flattens non-breaking spaces,
    which survive an HTML-rendered email into a hand-pasted CSV."""
    s = value if isinstance(value, str) else str(value)
    return _WS.sub(" ", s.replace(chr(0xa0), " ")).strip()


def _decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    m = _NUM.search(_text(value))
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace(",", "").replace("_", ""))
    except InvalidOperation:
        return None


def _amount(value: Any) -> Optional[Decimal]:
    """``numeric`` plus the email shorthand: ``750mm`` -> 750000000."""
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return _decimal(value)
    m = _AMOUNT.match(_text(value))
    if not m:
        return None
    try:
        base = Decimal(m.group(1).replace(",", "").replace("_", ""))
    except InvalidOperation:
        return None
    suffix = (m.group(2) or "").casefold()
    return base * _AMOUNT_MULTIPLIER.get(suffix, Decimal(1))


#: Month names as real emails print them — ``May 20, 2029``, ``Jul 07, 2032``,
#: ``12 June 2029``. Added 2026-08-11: an expectation stated in the email's own
#: words could not be parsed, fell back to text, and so could never equal the
#: epoch-milliseconds the application stores. Both date columns scored **0 of 11**
#: on the first real comparison, which reads as an agent that cannot get a single
#: date right. Fixing this in the normalizer rather than in the prefill is
#: deliberate: a date **typed by hand** off the email must work too.
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_MONTH_FIRST = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})$")
_DAY_FIRST = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})$")


def _month_name_date(s: str) -> Optional[date]:
    for pattern, order in ((_MONTH_FIRST, "mdy"), (_DAY_FIRST, "dmy")):
        m = pattern.match(s.strip())
        if not m:
            continue
        name, day, year = ((m.group(1), m.group(2), m.group(3)) if order == "mdy"
                           else (m.group(2), m.group(1), m.group(3)))
        month = _MONTHS.get(name[:3].lower())
        if not month:
            return None
        try:
            return date(int(year), month, int(day))
        except ValueError:
            return None
    return None


def _date_day(value: Any) -> Optional[date]:
    """epoch-ms / epoch-s / ISO / ``dd-MM-yyyy`` / ``May 20, 2029`` -> a UTC date.

    Both lanes hand over strings: the store keeps the generator's epoch
    milliseconds verbatim, a Postgres fetch produces ISO timestamps, and a
    hand-made CSV can carry either — plus the ``dd-MM-yyyy`` the app's exports
    use, and the month-name form every real broker email prints.
    """
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _epoch_to_date(Decimal(str(value)))

    s = _text(value)
    if not s:
        return None
    if _EPOCH.match(s):
        return _epoch_to_date(Decimal(s))
    m = _DMY.match(s)
    if m:
        d, mo, y = (int(g) for g in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = _YMD.match(s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    named = _month_name_date(s)
    if named is not None:
        return named
    iso = s.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.date()


def _epoch_to_date(raw: Decimal) -> Optional[date]:
    """Milliseconds above ~2001 in seconds, seconds below. 1e11 seconds is the
    year 5138, so nothing this app generates is ambiguous."""
    seconds = raw / 1000 if abs(raw) >= 10 ** 11 else raw
    try:
        return datetime.fromtimestamp(float(seconds), tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def _bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    s = _text(value).casefold()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def _list_set(value: Any) -> frozenset:
    """``'Barclays/BNP Paribas'`` == ``'BNP PARIBAS, BARCLAYS'`` (18.8)."""
    return frozenset(p.strip().upper() for p in _LIST_SPLIT.split(_text(value)) if p.strip())


def _ratings(value: Any) -> Tuple[str, ...]:
    """``'Baa2/BBB/BBB-'`` -> a multiset of agency ratings, order-insensitive,
    ``(Exp)`` / ``(EXP)`` suffixes dropped."""
    parts = [_PARENS.sub("", p).strip().upper()
             for p in _LIST_SPLIT.split(_text(value))]
    return tuple(sorted(p for p in parts if p))


def _tenor(value: Any) -> Optional[Decimal]:
    """``5Y`` == ``5 Y`` == ``5 Year`` == ``5``; months are folded to years."""
    m = _TENOR.match(_text(value))
    if not m:
        return None
    try:
        n = Decimal(m.group(1))
    except InvalidOperation:
        return None
    unit = (m.group(2) or "y").casefold()
    return n / 12 if unit.startswith("m") else n


def _vocab_lookup(column: Optional[str], text: str,
                  vocab: Optional[Dict[Tuple[str, str], Dict[str, str]]]) -> Tuple[str, bool]:
    """Generated text -> the value the app stores (18.8). Returns (value, mapped).

    A value that is already a known ``db_value`` for the column is left alone
    and counts as mapped: the actual lane arrives post-translation, and the
    expectation is written post-translation too (18.6.1), so only a genuinely
    unknown value is flagged.
    """
    if not column or not vocab:
        return text, False
    entry = vocab.get((column, text.casefold()))
    if entry and entry.get("db_value"):
        return entry["db_value"], True

    # Same lookup, ignoring formatting — a map entry written 'Semi-Annual' must
    # still be found when the app spelled it 'Semi annual'.
    key = _loose(text)
    for (col, gen), ent in vocab.items():
        if col != column:
            continue
        if _loose(gen) == key and ent.get("db_value"):
            return ent["db_value"], True
        if _loose(ent.get("db_value") or "") == key:
            return text, True
    return text, False


def _loose(value: Any) -> str:
    """Case-insensitive text with *formatting* differences flattened.

    The whole point of the comparison is that the agent parsed the email into the
    application's own vocabulary — so 'Semi annual' against 'Semi-Annual', or
    'Senior Unsecured' against 'Senior-Unsecured', is a success, not a failure.
    Only presentation is flattened here:

    * case
    * hyphens and underscores, which stand in for a space
    * whitespace around a slash, so '30 / 360' == '30/360'
    * repeated whitespace

    Deliberately NOT flattened: all whitespace (so 'SemiAnnual' stays distinct
    from 'Semi Annual'), and parenthetical qualifiers (so 'Actual/Actual (ICMA)'
    stays distinct from 'Actual/Actual' — ICMA and ISDA are different day-count
    conventions and collapsing them would hide a real defect). Those are genuine
    synonyms rather than formatting, and belong in ``vocab_map.csv``; the
    mismatch note names the exact pair to add.
    """
    s = _text(value).casefold()
    s = s.replace("-", " ").replace("_", " ")
    s = _SLASH_WS.sub("/", s)
    return _WS.sub(" ", s).strip()


_SIMPLE: Dict[str, Callable[[Any], Any]] = {
    "text": _text,
    "ci_text": _loose,
    "numeric": _decimal,
    "amount": _amount,
    "date_day": _date_day,
    "bool": _bool,
    "list_set": _list_set,
    "ratings": _ratings,
    "tenor": _tenor,
}

NORMALIZERS: Tuple[str, ...] = tuple(_SIMPLE) + ("vocab",)


def normalize(value: Any, normalizer: str = "text", *, column: Optional[str] = None,
              vocab: Optional[Dict[Tuple[str, str], Dict[str, str]]] = None) -> Canon:
    """Reduce one value to its comparable form (spec 18.8).

    An unparseable value falls back to the case-insensitive text form rather
    than raising, so a malformed date still compares equal to an identical
    malformed date instead of blowing up a whole compare pass.
    """
    kind = normalizer or "text"
    if is_null(value):
        return Canon(kind, None, None, null=True)
    text = _text(value)

    if kind == "vocab":
        mapped, ok = _vocab_lookup(column, text, vocab)
        return Canon(kind, _loose(mapped), text, unmapped=not ok)

    fn = _SIMPLE.get(kind)
    if fn is None:                       # unknown normalizer: behave like ci_text
        return Canon("ci_text", text.casefold(), text)
    canon = fn(value)
    if canon is None:                    # unparseable for this normalizer
        return Canon(kind, text.casefold(), text)
    return Canon(kind, canon, text)


def canon_equal(a: Canon, b: Canon, *, tolerance_days: int = 0) -> bool:
    """Equality between two canonical values, honouring the epsilon/tolerance
    rules of the numeric and date normalizers."""
    if a.null or b.null:
        return a.null and b.null
    av, bv = a.value, b.value
    if isinstance(av, Decimal) and isinstance(bv, Decimal):
        return abs(av - bv) <= _NUMERIC_EPSILON
    if isinstance(av, date) and isinstance(bv, date):
        return abs((av - bv).days) <= max(0, int(tolerance_days))
    if type(av) is not type(bv):
        # One side failed to parse and fell back to text. Compare textually so a
        # genuinely identical pair still matches.
        return _text(a.text or "").casefold() == _text(b.text or "").casefold()
    return av == bv


# ── configuration helpers ─────────────────────────────────────────────────────

def _config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    merged.update(config or {})
    merged["date_tolerance_days"] = {str(k): int(v) for k, v in
                                     (merged.get("date_tolerance_days") or {}).items()}
    return merged


def _tolerance(cfg: Dict[str, Any], column: str) -> int:
    tol = cfg["date_tolerance_days"]
    return int(tol.get(column, tol.get("default", 0)))


def _maps(maps: Optional[Dict[str, Any]], asset_class: str) -> Dict[str, Any]:
    """Field/vocab maps, loaded once by the caller where possible.

    The import is local so this module stays importable (and testable) without
    touching the store; ``effective_map`` reads degrade to the seed maps when no
    store exists, so this never creates a database as a side effect.
    """
    if maps is not None:
        return maps
    from expected_store import effective_map
    return effective_map(asset_class)


# ── per-field comparison ──────────────────────────────────────────────────────

#: What a field actually tests (spec 19.20.1, D17). Roughly 27 of the 72 compared
#: concepts are not agent extractions at all, and blending them into one number
#: moves it for reasons no prompt change can address — a derived mismatch sends
#: somebody to edit a prompt that was never involved.
#:
#: * ``extracted`` — the agent read it out of the email. The real target, and the
#:   only provenance a prompt change can improve.
#: * ``derived``   — the application computed it from what the agent extracted
#:   (confirmed for tranche-level ``registration_type``).
#: * ``stamped``   — the pipeline wrote a constant. A plumbing check.
PROVENANCE: Tuple[str, ...] = ("extracted", "derived", "stamped")
_PROVENANCE_BY_SOURCE = {
    "payload": "extracted", "pool": "extracted", "email_meta": "extracted",
    "derived": "derived",
    "const": "stamped", "system": "stamped",
}


def provenance_of(source_kind: Optional[str]) -> str:
    """Which of the three questions a field answers. Unknown reads as extracted —
    the conservative choice, since it keeps the field in the prompt backlog."""
    return _PROVENANCE_BY_SOURCE.get((source_kind or "").strip(), "extracted")


@dataclass
class Finding:
    """One field of one row pair — the shape ``util_comparison_field`` stores."""
    table: str
    column: str
    tier: str
    normalizer: str
    in_email: bool
    util_deal_seq: Optional[int]
    util_tranche_seq: Optional[int]
    match_confidence: Optional[str]
    expected_value: Optional[str]
    actual_value: Optional[str]
    verdict: str
    note: str = ""
    #: Not persisted: it is a property of `field_map.csv`, not of the pass, so it
    #: is recomputed on read and a map correction applies to old passes too.
    source_kind: str = ""
    #: False when the expected row this field came from was **paired with nothing**.
    #: Those fields are all trivially `missing`, and counting them as extraction
    #: failures conflates *coverage* with *accuracy* — see `score`.
    row_matched: bool = True

    @property
    def provenance(self) -> str:
        return provenance_of(self.source_kind)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table, "column": self.column, "tier": self.tier,
            "normalizer": self.normalizer, "in_email": self.in_email,
            "util_deal_seq": self.util_deal_seq,
            "util_tranche_seq": self.util_tranche_seq,
            "match_confidence": self.match_confidence,
            "expected_value": self.expected_value,
            "actual_value": self.actual_value,
            "verdict": self.verdict, "note": self.note,
            "provenance": self.provenance, "row_matched": self.row_matched,
        }


def compare_field(column: str, entry: Dict[str, str], expected: Any, actual: Any, *,
                  vocab: Optional[Dict[Tuple[str, str], Dict[str, str]]] = None,
                  tolerance_days: int = 0) -> Tuple[str, str]:
    """Verdict for one column: ``(verdict, note)``.

    Four outcomes and nothing else (spec 18.8):

    ``match``      the agent's value agrees, exactly or after normalisation —
                   'Semi annual' against 'Semi-Annual' is a success, because
                   parsing the email into the application's own vocabulary is
                   precisely what the agent is for.
    ``mismatch``   both sides have a value and they genuinely differ.
    ``missing``    the utility expected a value and the agent produced none.
    ``extra``      the utility expected nothing and the agent produced something.
                   Reported, never scored — usually informative, occasionally a
                   hallucination, always a human judgement.

    Columns the map marks tier ``x`` are not compared at all.
    """
    tier = (entry.get("tier") or "x").strip()
    norm = (entry.get("normalizer") or "ci_text").strip() or "ci_text"
    e_null, a_null = is_null(expected), is_null(actual)

    if tier not in ("1", "2", "3"):
        return (EXTRA if (e_null and not a_null) else NOT_COMPARED), ""
    if e_null and a_null:
        return NOT_COMPARED, ""
    if e_null:
        return EXTRA, ""
    if a_null:
        return MISSING, ""

    ce = normalize(expected, norm, column=column, vocab=vocab)
    ca = normalize(actual, norm, column=column, vocab=vocab)
    if canon_equal(ce, ca, tolerance_days=tolerance_days):
        return MATCH, ""

    note = ""
    if norm == "vocab" and (ce.unmapped or ca.unmapped):
        note = (f"add to vocab_map.csv: {column} — the email's {ce.text!r} and the "
                f"app's {ca.text!r} are not linked")
    return MISMATCH, note


def _display(value: Any) -> Optional[str]:
    return None if is_null(value) else (value if isinstance(value, str) else str(value))


def compare_row(table: str, expected: Dict[str, Any], actual: Optional[Dict[str, Any]],
                *, maps: Dict[str, Any], cfg: Dict[str, Any],
                rendered: Optional[frozenset] = None,
                confidence: Optional[str] = None) -> List[Finding]:
    """Every compared column of one expected row against its matched actual row.

    ``actual`` of ``None`` means the agent produced no matching row at all, so
    every populated column counts as missing.
    """
    findings: List[Finding] = []
    vocab = maps["vocab"]
    for (t, column), entry in maps["fields"].items():
        if t != table:
            continue
        # Tier-x columns are not skipped here: compare_field still reports one
        # the agent populated as `extra`, which is worth seeing in the diff even
        # though it never moves the score.
        exp = expected.get(column)
        act = None if actual is None else actual.get(column)
        verdict, note = compare_field(column, entry, exp, act, vocab=vocab,
                                      tolerance_days=_tolerance(cfg, column))
        if verdict == NOT_COMPARED:
            continue
        findings.append(Finding(
            table=table, column=column, tier=(entry.get("tier") or "x"),
            normalizer=(entry.get("normalizer") or "ci_text"),
            in_email=(rendered is None or column in rendered),
            util_deal_seq=expected.get("util_deal_seq"),
            util_tranche_seq=expected.get("util_tranche_seq"),
            match_confidence=confidence,
            expected_value=_display(exp), actual_value=_display(act),
            verdict=verdict, note=note,
            source_kind=(entry.get("source_kind") or ""),
            row_matched=actual is not None))
    return findings


# ── row matching (spec 18.7) ──────────────────────────────────────────────────

@dataclass
class RowPair:
    expected: Dict[str, Any]
    actual: Optional[Dict[str, Any]]
    key: Optional[str] = None
    confidence: Optional[str] = None
    ambiguous: bool = False


@dataclass
class MatchResult:
    pairs: List[RowPair] = dc_field(default_factory=list)
    unmatched_actual: List[Dict[str, Any]] = dc_field(default_factory=list)

    @property
    def matched(self) -> int:
        return sum(1 for p in self.pairs if p.actual is not None)

    @property
    def unmatched_expected(self) -> int:
        return sum(1 for p in self.pairs if p.actual is None)


def _first(row: Dict[str, Any], *columns: str) -> Optional[str]:
    for c in columns:
        v = row.get(c)
        if not is_null(v):
            return _text(v)
    return None


def _key_identifier(row: Dict[str, Any]) -> Optional[str]:
    """Key 1 — an ISIN or CUSIP is unique per tranche."""
    v = _first(row, "isin", "sec_144a_isin", "sec_regs_isin", "sec_other_isin",
               "cusip", "sec_144a_cusip", "sec_regs_cusip", "sec_other_cusip")
    return v.upper() if v else None


def _key_ticker(row: Dict[str, Any]) -> Optional[str]:
    v = _first(row, "issuer_ticker")
    return v.upper() if v else None


def _key_ticker_ccy_tenor(row: Dict[str, Any]) -> Optional[str]:
    """Key 2 — all three parts required. Ticker + currency alone is shared by
    every tranche of a single-currency deal and would pair rows arbitrarily."""
    tick = _key_ticker(row)
    ccy = _first(row, "currency_code", "tranche_currency")
    tenor = _first(row, "preliminary_security_tenor", "tenor")
    if not (tick and ccy and tenor):
        return None
    t = normalize(tenor, "tenor")
    return f"{tick}|{ccy.upper()}|{t.value if t.value is not None else _loose(tenor)}"


def _key_maturity_size(row: Dict[str, Any]) -> Optional[Tuple[Any, ...]]:
    """Key 3 — ticker + maturity (±tolerance) + size."""
    tick = _key_ticker(row)
    mat = normalize(row.get("maturity_date"), "date_day")
    size = normalize(row.get("total_issued_amount"), "amount")
    if not tick or mat.null or size.null:
        return None
    return (tick, mat.value, size.value)


def match_rows(table: str, expected_rows: Sequence[Dict[str, Any]],
               actual_rows: Sequence[Dict[str, Any]], *,
               maturity_tolerance_days: int = 3) -> MatchResult:
    """Pair expected rows with the agent's rows on business keys (spec 18.7).

    App-assigned ids are useless across sources, so matching walks the keys in
    priority order, consuming each actual row once. Deal rows have no tranche
    key, so they match on ticker alone.
    """
    result = MatchResult()
    remaining = list(actual_rows)

    def take(pred) -> Tuple[Optional[Dict[str, Any]], bool]:
        hits = [r for r in remaining if pred(r)]
        if not hits:
            return None, False
        remaining.remove(hits[0])
        return hits[0], len(hits) > 1

    for exp in expected_rows:
        pair = RowPair(expected=exp, actual=None)

        ident = _key_identifier(exp)
        if ident:
            got, amb = take(lambda r: _key_identifier(r) == ident)
            if got is not None:
                pair.actual, pair.key, pair.confidence, pair.ambiguous = \
                    got, "identifier", "exact", amb

        if pair.actual is None and table == "issuance_deal":
            tick = _key_ticker(exp)
            if tick:
                got, amb = take(lambda r: _key_ticker(r) == tick)
                if got is not None:
                    pair.actual, pair.key, pair.confidence, pair.ambiguous = \
                        got, "ticker", "high", amb

        if pair.actual is None:
            k2 = _key_ticker_ccy_tenor(exp)
            if k2:
                got, amb = take(lambda r: _key_ticker_ccy_tenor(r) == k2)
                if got is not None:
                    pair.actual, pair.key, pair.confidence, pair.ambiguous = \
                        got, "ticker+currency+tenor", "high", amb

        if pair.actual is None:
            k3 = _key_maturity_size(exp)
            if k3:
                def near(r):
                    o = _key_maturity_size(r)
                    return (o is not None and o[0] == k3[0] and o[2] == k3[2]
                            and abs((o[1] - k3[1]).days) <= maturity_tolerance_days)
                got, amb = take(near)
                if got is not None:
                    pair.actual, pair.key, pair.confidence, pair.ambiguous = \
                        got, "ticker+maturity+size", "high", amb

        if pair.actual is None:
            tick = _key_ticker(exp)
            if tick and len([r for r in remaining if _key_ticker(r) == tick]) == 1:
                got, _ = take(lambda r: _key_ticker(r) == tick)
                pair.actual, pair.key, pair.confidence = got, "ticker", "low"

        result.pairs.append(pair)

    result.unmatched_actual = remaining
    return result


# ── scoring (spec 18.8) ───────────────────────────────────────────────────────

def _counts() -> Dict[str, int]:
    return {v: 0 for v in VERDICTS}


def _pct(right: int, wrong: int) -> Optional[float]:
    total = right + wrong
    return None if total == 0 else round(right / total, 6)


def score(findings: Sequence[Finding]) -> Dict[str, Any]:
    """The one number, plus the counts that explain it.

    Accuracy is measured over the fields the email actually rendered
    (``in_email``), because a field the template never printed cannot be
    extracted and counting it would report a template limit as a model failure
    (spec 18.6). Fields excluded for that reason are reported alongside, so the
    number is never quietly narrowed.

    **Fields of an expected row that matched nothing are excluded too**, and
    reported as ``fields_in_unmatched_rows``. Every one of them is trivially
    ``missing`` — there was no row to hold a value — so counting them measures
    *coverage*, not accuracy, and spec 18.7 already says row-matching failures are
    surfaced as their own problem rather than as a low score. Measured on real agent
    output: a run of ten emails of which the agent had processed two scored **27.5%**
    with them included and **54.6%** without. The first number says the agent is bad;
    the true statement is that eight emails were never ingested. The row counts
    (``rows_missing``) carry that fact, and the diff still shows every field.
    """
    counts = _counts()
    right = wrong = 0
    not_in_email = 0
    in_unmatched = 0
    split = {p: {"matched": 0, "compared": 0} for p in PROVENANCE}
    for f in findings:
        counts[f.verdict] += 1
        if f.verdict not in SCORED_RIGHT and f.verdict not in SCORED_WRONG:
            continue
        if not f.row_matched:
            in_unmatched += 1
            continue
        if not f.in_email:
            not_in_email += 1
            continue
        bucket = split[f.provenance]
        bucket["compared"] += 1
        if f.verdict in SCORED_RIGHT:
            right += 1
            bucket["matched"] += 1
        else:
            wrong += 1
    for bucket in split.values():
        bucket["accuracy"] = _pct(bucket["matched"],
                                  bucket["compared"] - bucket["matched"])
    return {
        "accuracy": _pct(right, wrong),
        "fields_matched": right,
        "fields_compared": right + wrong,
        "fields_not_in_email": not_in_email,
        "fields_in_unmatched_rows": in_unmatched,
        "verdicts": counts,
        # D17: the headline blends three different questions, so it is reported
        # beside the three. `extracted` is the one a prompt change can move.
        "by_provenance": split,
    }


def compare_tables(expected: Dict[str, Sequence[Dict[str, Any]]],
                   actual: Dict[str, Sequence[Dict[str, Any]]], *,
                   maps: Optional[Dict[str, Any]] = None,
                   asset_class: str = "bonds",
                   config: Optional[Dict[str, Any]] = None,
                   rendered_fields: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Match and compare every table. Returns findings plus per-table row counts."""
    cfg = _config(config)
    m = _maps(maps, asset_class)
    rendered = None if rendered_fields is None else frozenset(rendered_fields)

    findings: List[Finding] = []
    tables: Dict[str, Any] = {}
    for table in TABLES:
        exp_rows = list(expected.get(table) or [])
        act_rows = list(actual.get(table) or [])
        if not exp_rows and not act_rows:
            continue
        res = match_rows(table, exp_rows, act_rows,
                         maturity_tolerance_days=_tolerance(cfg, "maturity_date"))
        for pair in res.pairs:
            findings.extend(compare_row(table, pair.expected, pair.actual,
                                        maps=m, cfg=cfg, rendered=rendered,
                                        confidence=pair.confidence))
        tables[table] = {
            "expected": len(exp_rows), "actual": len(act_rows),
            "matched": res.matched,
            "unmatched_expected": res.unmatched_expected,
            "unmatched_actual": len(res.unmatched_actual),
            "pairs": [{"util_deal_seq": p.expected.get("util_deal_seq"),
                       "util_tranche_seq": p.expected.get("util_tranche_seq"),
                       "matched": p.actual is not None,
                       "key": p.key, "confidence": p.confidence,
                       "ambiguous": p.ambiguous} for p in res.pairs],
        }
    return {"findings": findings, "tables": tables, "config": cfg, "maps": m}


def compare_run(expected: Dict[str, Sequence[Dict[str, Any]]],
                actual: Dict[str, Sequence[Dict[str, Any]]], *,
                maps: Optional[Dict[str, Any]] = None,
                asset_class: str = "bonds",
                config: Optional[Dict[str, Any]] = None,
                rendered_fields: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """One compare pass over a capture run — the entry point for the router.

    ``expected`` / ``actual`` are ``{table: [row dicts]}`` of app column names
    plus the store's ``util_*`` metadata. ``rendered_fields`` is the union of
    ``util_email.rendered_fields`` for the run.
    """
    detail = compare_tables(expected, actual, maps=maps, asset_class=asset_class,
                            config=config, rendered_fields=rendered_fields)
    findings = detail["findings"]
    scores = score(findings)

    matched = unmatched_e = unmatched_a = 0
    for t in detail["tables"].values():
        matched += t["matched"]
        unmatched_e += t["unmatched_expected"]
        unmatched_a += t["unmatched_actual"]
    scores.update({"rows_matched": matched,
                   "rows_missing": unmatched_e,
                   "rows_unexpected": unmatched_a})

    return {
        "asset_class": asset_class,
        "scores": scores,
        "tables": detail["tables"],
        "findings": [f.as_dict() for f in findings],
        "worst_columns": worst_columns(findings),
    }


def worst_columns(findings: Sequence[Finding], limit: int = 20) -> List[Dict[str, Any]]:
    """Per ``table.column``, worst first — which field does the agent get wrong
    most often. The prompt-improvement backlog."""
    acc: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for f in findings:
        if not f.in_email:
            continue
        if f.verdict not in SCORED_RIGHT and f.verdict not in SCORED_WRONG:
            continue
        a = acc.setdefault((f.table, f.column),
                           {"table": f.table, "column": f.column,
                            "tier": f.tier, "right": 0, "wrong": 0})
        a["right" if f.verdict in SCORED_RIGHT else "wrong"] += 1
    rows = []
    for a in acc.values():
        if not a["wrong"]:
            continue
        a["accuracy"] = _pct(a["right"], a["wrong"])
        rows.append(a)
    rows.sort(key=lambda r: (r["accuracy"], -r["wrong"]))
    return rows[:limit]
