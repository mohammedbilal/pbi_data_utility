"""The labelling form for an uploaded email (spec 19.6, build step B2).

An uploaded email has no payload behind it, so its expected values are **stated by
a human**. This module builds the form they state them in, and pre-fills what can
be read from the email deterministically.

Three rules shape everything here:

* **Blank means the email did not say it** (D12) — excluded from scoring, not
  counted as a miss. So the form must be short enough that filling only what is
  present is the natural thing to do.
* **Prefill comes from the email text, never from the rows the application
  stored** (D11). Reading the email faster cannot corrupt the answer; confirming
  the agent's own output would score agreement rather than accuracy.
* **A suggestion is not an answer.** Everything here is returned as
  ``suggestions``, separate from ``label``; only a save commits it.

The default field list is **derived from the ten real sample emails**, not guessed:
their table row labels were counted, and every label appearing in two or more
emails that maps to a compared column is included (19.23).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import email_ingest

#: Grain -> the columns the form offers by default, in the order it shows them.
#: Ordered by how the emails themselves read, not by tier, because the person
#: filling this in is reading down an email.
DEFAULT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "deal": (
        "issuer_name", "issuer_ticker", "registration_type",
        "regulation_subcategory", "use_of_proceeds", "bookrunners", "bnd_bank",
        "issuer_rating", "rating_outlook", "deal_currencies", "announcement_dt",
    ),
    "tranche": (
        "currency_code", "tranche_currency", "preliminary_security_tenor", "tenor",
        "maturity_date", "is_perpetual", "coupon_type", "coupon_frequency",
        "bond_seniority", "ipts", "total_issued_amount", "tranche_settlement_date",
        "tranche_settlement_period", "bond_class", "call_indicator",
        "is_esg", "esg_type",
    ),
    "security": ("isin", "cusip", "figi", "registration_type",
                 "regulation_subcategory"),
}

#: Which table each grain is read from when describing a field. `issuance_data`
#: carries every tranche column including the seven `t_issuance` lacks (18.2).
GRAIN_TABLE = {"deal": "issuance_deal", "tranche": "issuance_data",
               "security": "issuance_security"}

#: Columns that are not scored but are needed to pair rows (19.6). Shown with
#: their purpose stated so nobody wonders why an unscored field is being asked for.
MATCHING_ONLY = {"preliminary_security_tenor"}

#: Real emails use vocabulary the columns do not. A labeller who does not make the
#: leap leaves the field blank — and a blank is silently excluded (D12) rather than
#: flagged, so these hints are the cheap mitigation (19.18).
HINTS: Dict[str, str] = {
    "coupon_type": "Emails print this as Coupon Type — 'Fixed to Float', "
                   "'Fixed Rate Reset', 'Floating'.",
    "coupon_frequency": "A perpetual states this as Dividend Payment Dates; "
                        "quarterly/semi-annual dates mean the frequency.",
    "bond_seniority": "Printed as Ranking — 'Sr Unsecured Note', "
                      "'Senior Bank Note', 'Subordinated Note'.",
    "ipts": "Printed as IPT or IPTs — 'T + 75a', 'SOFR Equiv', '7.625%'.",
    "total_issued_amount": "Only when a number is stated. 'USD Benchmark' or "
                           "'$Benchmark' is a placeholder, so leave this blank.",
    "maturity_date": "Leave blank and tick perpetual if the email says Perpetual.",
    "is_perpetual": "A perpetual bond states Maturity: Perpetual and has no date.",
    "tranche_settlement_date": "Printed as Settlement — take the date, not the T+n.",
    "tranche_settlement_period": "The T+n label itself, e.g. 'T+5'.",
    "registration_type": "From the Format row. '3a2' is NOT this — it is the "
                         "regulation sub-category.",
    "regulation_subcategory": "From the Format row: '3a2', or the 'with Reg "
                              "Rights' part of a compound format.",
    "issuer_rating": "The agency ratings without the outlook, e.g. 'A2/A+/AA-'.",
    "rating_outlook": "The outlook only, e.g. 'Stable'.",
    "bnd_bank": "The bookrunner marked (B&D) — billing and delivery.",
    "bookrunners": "The syndicate list without the (B&D) marker.",
    "esg_type": "ING's 'ESG Theme: Green' means Green.",
    "call_indicator": "True when the email states any call — Make Whole, Par "
                      "Call, or an NC period in the tenor.",
}


def build_form(maps: Dict[str, Any], *, full: bool = False) -> List[Dict[str, Any]]:
    """The form's field list, grouped by grain.

    ``full`` returns every compared column rather than the default set — the
    *show all* escape hatch. The default is about 30 of the 72 compared concepts,
    which is what the real emails actually speak to.
    """
    fields: Dict[Tuple[str, str], Dict[str, str]] = maps["fields"]
    vocab: Dict[Tuple[str, str], Dict[str, str]] = maps["vocab"]
    groups: List[Dict[str, Any]] = []

    for grain, defaults in DEFAULT_FIELDS.items():
        table = GRAIN_TABLE[grain]
        if full:
            columns = sorted({col for (t, col), e in fields.items()
                              if t == table and (e.get("tier") in ("1", "2", "3")
                                                 or col in MATCHING_ONLY)})
            # Keep the curated order first, then everything else alphabetically.
            columns = list(defaults) + [c for c in columns if c not in defaults]
        else:
            columns = list(defaults)

        items = []
        for column in columns:
            entry = fields.get((table, column))
            if entry is None:
                continue
            tier = entry.get("tier") or "x"
            normalizer = (entry.get("normalizer") or "").strip()
            items.append({
                "column": column,
                "tier": tier,
                "normalizer": normalizer,
                "scored": tier in ("1", "2", "3"),
                "matching_only": column in MATCHING_ONLY,
                "options": _vocab_options(vocab, column) if normalizer == "vocab" else [],
                "max_length": _max_length(maps, table, column),
                "hint": HINTS.get(column, ""),
            })
        groups.append({"grain": grain, "table": table, "fields": items})
    return groups


def _vocab_options(vocab: Dict[Tuple[str, str], Dict[str, str]],
                   column: str) -> List[str]:
    """The application's own values for a column, so the labeller picks rather
    than types and normalization is never in play (19.6)."""
    out = {entry["db_value"] for (col, _gen), entry in vocab.items()
           if col == column and entry.get("db_value")}
    return sorted(out)


def _max_length(maps: Dict[str, Any], table: str, column: str) -> Optional[int]:
    try:
        import expected_store
        return expected_store.load_schema(table).get(column)
    except Exception:                                        # pragma: no cover
        return None


# ── prefill ───────────────────────────────────────────────────────────────────
# Everything below reads the EMAIL. Nothing reads the application's rows (D11).

#: Email row label -> the column it fills. Derived by counting the row labels of
#: the ten real samples (19.23); every label here appears in at least two of them.
LABEL_TO_COLUMN: Dict[str, str] = {
    "issuer/ticker": "issuer_name",
    "issuer": "issuer_name",
    "ranking": "bond_seniority",
    "ipt": "ipts",
    "ipts": "ipts",
    "coupon type": "coupon_type",
    "maturity date": "maturity_date",
    "maturity": "maturity_date",
    "tenor": "preliminary_security_tenor",
    "total size": "total_issued_amount",
    "tranche size": "total_issued_amount",
    "size": "total_issued_amount",
    "use of proceeds": "use_of_proceeds",
    "settlement": "tranche_settlement_date",
    "format": "registration_type",
    "ratings": "issuer_rating",
    "expected security ratings": "issuer_rating",
    "issuer ratings": "issuer_rating",
    "book runner(s)": "bookrunners",
    "active bookrunners": "bookrunners",
    "joint bookrunners": "bookrunners",
    "esg theme": "esg_type",
    "security": "bond_seniority",
}

_PERPETUAL_RE = re.compile(r"\bperpetual\b", re.I)
_BENCHMARK_RE = re.compile(r"benchmark|tbd|\[|●", re.I)
_TICKER_IN_NAME_RE = re.compile(r"^(.*?)\(\s*([A-Z][A-Z0-9.\-]{0,8})\s*\)\s*$")
_OUTLOOK_RE = re.compile(r"\b(stable|positive|negative|developing)\b", re.I)
_TPLUS_RE = re.compile(r"\bT\s*\+\s*(\d+)\b", re.I)
_DATE_RE = re.compile(
    r"\b((?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+\d{1,2},?\s+\d{4}|\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4}|"
    r"[A-Za-z]{3}\s+\d{1,2},?\s+\d{4})\b")
_NC_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*[-–]?\s*NC", re.I)
_CALL_RE = re.compile(r"make whole|par call|standard call|optional redemption|"
                      r"\bNC\d|redeem", re.I)


def suggest(body_html: str, body_text: str,
            identifiers: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    """Deterministic prefill for one email (19.6.1).

    Returns ``{"deal": {...}, "tranches": [{...}, ...], "notes": [...]}`` — the same
    shape as a label, so the form can drop it straight into empty fields. Values are
    **suggestions**: the form marks them as unconfirmed and only a save commits them.

    Reads the email's table rows where it has them (``label, value, value`` — one
    column per tranche) and falls back to ``Label: value`` lines otherwise. A row
    with one value against several tranches is a deal-level statement; a row with
    one value per tranche fills each tranche separately.
    """
    rows = email_ingest.html_tables(body_html or "")
    deal: Dict[str, Any] = {}
    tranches: List[Dict[str, Any]] = []
    notes: List[str] = []

    labelled: List[Tuple[str, List[str]]] = []
    for row in rows:
        if len(row) >= 2 and row[0].strip() and any(c.strip() for c in row[1:]):
            labelled.append((_norm_label(row[0]), [c.strip() for c in row[1:]]))
    if not labelled:
        labelled = _labelled_lines(body_text or "")

    width = max((len(v) for _l, v in labelled), default=0)
    for _ in range(max(1, width)):
        tranches.append({})

    for label, values in labelled:
        column = LABEL_TO_COLUMN.get(label)
        if not column:
            continue
        # One email can describe more than one deal — the USB sample states two
        # different legal issuers under one ticker (19.17). Prefill cannot split
        # them (how many deals an email describes is a labelling input), but it must
        # not merge them silently either: the second issuer's values would land in
        # the first deal's fields and look like the labeller's own choice.
        if column == "issuer_name":
            distinct = {re.sub(r"\s*\([^)]*\)\s*$", "", v).strip().casefold()
                        for v in values if v.strip()}
            if len(distinct) > 1:
                notes.append(
                    f"This email names {len(distinct)} different issuers "
                    f"({', '.join(sorted(distinct)[:3])}) — it describes more than "
                    f"one deal. Add a second deal and split the tranches between "
                    f"them; only the first issuer has been suggested.")
        # One value against several columns is a deal-level statement; the emails
        # merge that cell across the table.
        per_tranche = len([v for v in values if v]) > 1
        for index, raw in enumerate(values):
            if not raw:
                continue
            target = tranches[index] if per_tranche else None
            for col, value in _interpret(column, raw, notes):
                if value in (None, ""):
                    continue
                if col in _DEAL_COLUMNS:
                    deal.setdefault(col, value)
                elif target is not None:
                    target.setdefault(col, value)
                else:
                    for t in tranches:
                        t.setdefault(col, value)

    ids = identifiers or {}
    if ids.get("tickers"):
        deal.setdefault("issuer_ticker", ids["tickers"][0])

    tranches = [t for t in tranches if t]
    return {"deal": deal, "tranches": tranches, "notes": sorted(set(notes))}


def _labelled_lines(text: str) -> List[Tuple[str, List[str]]]:
    """``label -> [value per tranche]`` from an email that has no usable table.

    Two shapes both appear in the real samples and neither is a table:

    * **Repeated blocks** — the CAD sample prints ``Tenor: 3Y / Size: … / Maturity: …``
      five times over, once per tranche. The *n*-th occurrence of a label is the
      *n*-th tranche's value, so a repeated label extends the value list rather than
      overwriting it. Collapsing them would have reported a five-tranche deal as one.
    * **No colon at all** — the deliberately messy sample writes
      ``Issuer/Ticker Sumitomo Mitsui Financial Group Inc ( SUMIBK )``. Matching a
      *known* label at the start of a line is safe where a general
      ``word word value`` pattern would not be: the label list is closed.
    """
    seen: Dict[str, List[str]] = {}
    order: List[str] = []
    known = sorted(LABEL_TO_COLUMN, key=len, reverse=True)

    for line in text.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if not line or len(line) > 400:
            continue
        label = value = None
        m = re.match(r"^([A-Za-z][A-Za-z0-9 /()&.'*-]{2,34}?)\s*:\s*(\S.*)$", line)
        if m and _norm_label(m.group(1)) in LABEL_TO_COLUMN:
            label, value = _norm_label(m.group(1)), m.group(2).strip()
        else:
            lowered = line.lower()
            for candidate in known:
                if lowered.startswith(candidate):
                    rest = line[len(candidate):].lstrip(" :\t")
                    if rest:
                        label, value = candidate, rest
                    break
        if not label or not value:
            continue
        if label not in seen:
            seen[label] = []
            order.append(label)
        seen[label].append(value)
    return [(label, seen[label]) for label in order]


_DEAL_COLUMNS = {"issuer_name", "issuer_ticker", "use_of_proceeds", "bookrunners",
                 "bnd_bank", "issuer_rating", "rating_outlook", "registration_type",
                 "regulation_subcategory"}


def _norm_label(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().rstrip("*:").strip().lower()


def _interpret(column: str, raw: str, notes: List[str]) -> List[Tuple[str, Any]]:
    """One email value -> the column(s) it fills, in the app's own terms.

    Conservative on purpose: where the reading is ambiguous it suggests nothing and
    leaves the field for the human, because a confidently wrong suggestion is worse
    than an empty box (19.6.1).
    """
    text = re.sub(r"\s+", " ", raw).strip()
    if not text:
        return []

    if column == "issuer_name":
        m = _TICKER_IN_NAME_RE.match(text)
        if m:
            return [("issuer_name", m.group(1).strip()), ("issuer_ticker", m.group(2))]
        return [("issuer_name", text)]

    if column == "total_issued_amount":
        # The size row carries the **currency** as well as the amount, and often
        # only the currency ("USD Benchmark", "C$ Benchmark"). That matters far more
        # than the amount: currency is one of the three parts of matching key 2
        # (18.7), so without it an expected row cannot be paired with the
        # application's row at all. A round-trip test — exporting the expected lane
        # and importing it back as a perfect agent — scored **5%** purely because
        # no currency was suggested and only 2 of 22 rows matched.
        out: List[Tuple[str, Any]] = []
        ccy = _currency(text)
        if ccy:
            out.append(("currency_code", ccy))
            out.append(("tranche_currency", ccy))
        if _BENCHMARK_RE.search(text):
            notes.append("Size is stated as a placeholder ('Benchmark' / '[.]'), "
                         "so no amount is suggested — leave it blank (D12).")
            return out
        if not any(c.isdigit() for c in text):
            # `Tranche Size: USD` is a currency, not an amount.
            return out
        return out + [("total_issued_amount", text)]

    if column == "maturity_date":
        if _PERPETUAL_RE.search(text):
            return [("is_perpetual", True)]
        m = _DATE_RE.search(text)
        return [("maturity_date", m.group(1))] if m else []

    if column == "preliminary_security_tenor":
        m = _NC_RE.match(text)
        if m:
            # `3-NC2` is a 3-year tranche, non-call 2. The non-call part has no
            # compared column (`nc_till` is tier x), so only the tenor is suggested.
            return [("preliminary_security_tenor", m.group(1)),
                    ("tenor", m.group(1)), ("call_indicator", True)]
        m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:Y|YR|YEAR)?S?$", text, re.I)
        if m:
            return [("preliminary_security_tenor", m.group(1)), ("tenor", m.group(1))]
        return []

    if column == "tranche_settlement_date":
        out: List[Tuple[str, Any]] = []
        m = _DATE_RE.search(text)
        if m:
            out.append(("tranche_settlement_date", m.group(1)))
        t = _TPLUS_RE.search(text)
        if t:
            out.append(("tranche_settlement_period", f"T+{t.group(1)}"))
        return out

    if column == "issuer_rating":
        outlook = _OUTLOOK_RE.search(text)
        # Ratings are printed per agency with the outlook attached
        # ("Moody's (Exp): A2/ Stable"). The agency names and (Exp) are noise; the
        # ratings themselves and the outlook are two different columns (18.6.1).
        # The trailing boundary must not be `\b`: after matching `A+` the position
        # sits between two non-word characters (`+` and `/`), so `\b` fails and the
        # match backtracks to a bare `A` — silently dropping every +/- suffix and
        # turning `A2/A+/AA-` into `A2/A/AA`.
        ratings = re.findall(r"\b(Aaa|Aa[123]|A[123]|Baa[123]|Ba[123]|B[123]|"
                             r"Caa[123]|AAA|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|"
                             r"CCC[+-]?)(?![A-Za-z0-9])", text)
        out = []
        if ratings:
            # NOT deduplicated. Two agencies routinely assign the same grade — the
            # ORCL sample is Moody's Baa2, S&P BBB, Fitch BBB — and the `ratings`
            # normalizer compares a **multiset**, so dropping the repeat makes a
            # correct expectation unequal to the application's `Baa2/BBB/BBB`.
            # Deduplicating here is what produced that mismatch on every row.
            out.append(("issuer_rating", "/".join(ratings)))
        if outlook:
            out.append(("rating_outlook", outlook.group(1).title()))
        return out

    if column == "registration_type":
        return _interpret_format(text)

    if column == "bookrunners":
        # `Active: USB(B&D), JPM, RBCCM` — the (B&D) bank is its own column, and the
        # marker must come off the list or `list_set` reads it as a different bank.
        body = re.sub(r"^\s*active\s*:?\s*", "", text, flags=re.I)
        bnd = re.search(r"([A-Za-z0-9&.\- ]+?)\s*\(\s*B\s*&\s*D\s*\)", body)
        cleaned = re.sub(r"\s*\(\s*B\s*&\s*D\s*\)", "", body).strip(" ,;")
        out = [("bookrunners", cleaned)] if cleaned else []
        if bnd:
            out.append(("bnd_bank", bnd.group(1).strip(" ,;")))
        return out

    if column == "coupon_type":
        return [("coupon_type", text)]

    if column == "esg_type":
        return [("esg_type", text), ("is_esg", True)]

    if column == "use_of_proceeds":
        # The app's rule: 'general corporate purposes' anywhere wins (19.18).
        if re.search(r"general corporate purposes", text, re.I):
            return [("use_of_proceeds", "GENERAL CORPORATE PURPOSES")]
        notes.append("Use of proceeds does not say 'general corporate purposes' — "
                     "choose OTHER or REPAY OUTSTANDING BORROWINGS yourself.")
        return []

    if column == "bond_seniority":
        return [("bond_seniority", text)]

    return [(column, text)]


#: Currency symbols the samples use. `C$` must be tested before a bare `$`, or a
#: Canadian deal reads as USD.
_CURRENCY_SYMBOLS = (("c$", "CAD"), ("a$", "AUD"), ("us$", "USD"), ("$", "USD"),
                     ("€", "EUR"), ("£", "GBP"), ("¥", "JPY"))
_CURRENCY_CODES = ("USD", "EUR", "GBP", "CAD", "JPY", "CHF", "AUD", "SEK", "NOK",
                   "HKD", "SGD", "CNY", "CNH", "NZD", "DKK")


def _currency(text: str) -> Optional[str]:
    """The ISO code a size row states, from a code or a symbol."""
    upper = text.upper()
    for code in _CURRENCY_CODES:
        if re.search(rf"\b{code}\b", upper):
            return code
    lowered = text.lower()
    for symbol, code in _CURRENCY_SYMBOLS:
        if symbol in lowered:
            return code
    return None


def _interpret_format(text: str) -> List[Tuple[str, Any]]:
    """The Format row fills registration_type, regulation_subcategory, or both.

    One printed row, two possible destinations, decided by the value (19.19.2).
    """
    out: List[Tuple[str, Any]] = []
    lowered = text.lower()
    if re.search(r"3\s*\(?\s*a\s*\)?\s*2", lowered):
        out.append(("regulation_subcategory", "3(a)2"))
    elif re.search(r"144a\s*/\s*regs?\b|144a\s*/\s*reg\s*s", lowered):
        out.append(("registration_type", "144A/Reg S"))
    elif re.search(r"\bsec[\s-]*registered\b", lowered):
        out.append(("registration_type", "Sec Registered"))
    elif re.search(r"^\s*144a\s*$", lowered):
        out.append(("registration_type", "144A"))
    elif re.search(r"^\s*reg\s*s\s*$", lowered):
        out.append(("registration_type", "Reg S"))
    # `reg\w*` rather than `reg(istration)?`: the SpaceX sample prints
    # "with Registation Rights" — the typo is in the source email, and an
    # expectation that only matches correct spelling would silently miss it.
    if re.search(r"with(out)?\s+reg\w*\s+rights", lowered):
        out.append(("regulation_subcategory",
                    "Without Reg Rights" if "without" in lowered else "With Reg Rights"))
    return out


def label_from_form(deal: Dict[str, Any], tranches: Sequence[Dict[str, Any]],
                    securities: Sequence[Sequence[Dict[str, Any]]]) -> Dict[str, Any]:
    """Assemble the shape ``expected_writer.from_label`` expects from form state."""
    return {"deals": [{
        "deal": dict(deal or {}),
        "tranches": [{"columns": dict(t or {}),
                      "securities": list(securities[i]) if i < len(securities) else []}
                     for i, t in enumerate(tranches or [])],
    }]}
