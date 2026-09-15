"""Securitized (ABS) engine — spec §20.

One deal is one POST of ``EVENT_CREATE_NEW_SECURITIZED_ISSUANCE``. The event carries the
whole tree: ``DETAILS`` (deal) -> ``SERIES`` -> ``TRANCHES`` -> ``SECURITIES``, every child
collection a **map keyed by its own id** rather than an array (§20.3.1).

Shape of this module, in the order the run uses it:

* ``ReferenceData``   — loads ``backend/reference/securitized/*.csv``; every file optional
  with a hardcoded fallback (§20.5.1).
* ``DealGenerator``   — deal -> series -> tranche -> security as plain nested lists, so the
  generation logic stays readable; the map serialisation happens at the end (§20.3.1).
* ``check_payload``   — the pre-flight assertion pass: §20.3.7 mandatory fields (errors) and
  §20.6 internal consistency (warnings). Runs before every POST, live or dry.
* ``_serialise``      — list-of-lists -> the ``TEMP-*`` map form, coercing the §20.3.6
  string-typed numerics through one explicit ``_STRING_FIELDS`` set so the two sides
  cannot drift.
* ``run_securitized`` — the engine contract (`CLAUDE.md`): log_queue dicts only, stop_event
  before every HTTP call and inside every delay, summary + ``None`` sentinel at the end.

Auth and envelope are identical to Loans (§20.2): token in the ``SESSION_AUTH_TOKEN``
header, constant ``SOURCE_REF: 12345``, three-key body, success is ``EVENT_ACK``.

The generator is **not** seeded — it uses the module-level ``random`` like
``bonds_engine``/``loans_engine`` so consecutive runs differ. ``reference/securitized/
_generate.py`` (which *is* seeded) is a one-off builder and is never called from here.

Deviations from §20 taken by this slice are listed in §20.19.2's slice-2 row and in
``master/implementation.md``; the two that change emitted data are:

* ``PREPAYMENT_TYPE``/``PRICING_SPEED`` are **omitted** for the 20 asset types whose
  ``asset_types.csv`` row carries the sentinel ``None``/``0% CPR`` (§20.5.3 already omits
  inapplicable statistics; emitting the literal string ``"None"`` would be worse data).
* Ladder steps (spread, coupon, underwriting discount) are **floored so the class-boundary
  jump always exceeds the within-senior-stack term premium** (§20.6.4). Drawing both
  independently lets ``A-4`` overtake class ``B`` on a 4-piece senior stack, which breaks
  the monotonic-rise rule the same section states.
"""
from __future__ import annotations

import csv
import json
import queue
import random
import re
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from engines.abs_utils import dates as D
from engines.abs_utils.identifiers import IdentifierPool, cusip as make_cusip, isin_144a, isin_regs_us

# ── constants ──────────────────────────────────────────────────────────────────

MESSAGE_TYPE = "EVENT_CREATE_NEW_SECURITIZED_ISSUANCE"
SERVICE_NAME = "ISSUANCE_EVENT_HANDLER"

TRADE_DESK = "Securitized"
DEAL_COVENANT = "Standard ABS"
SERIES_COVENANT = "Standard securitization covenants"
INVESTOR_STATUS = "OPEN"
RATING_AGENCIES = "Moody's / S&P / Fitch"
PRICE_TYPE = "Fixed price re-offer"
IPO_PRICE = "100"
SECURITY_FORMAT = "Book Entry"
SETTLEMENT_TYPE = "DVP"
MINIMUM_PIECE = 1000
MINIMUM_INCREMENT = 1000

DEAL_SIZE_LADDER = [
    250_000_000, 300_000_000, 350_000_000, 400_000_000, 500_000_000, 600_000_000,
    750_000_000, 850_000_000, 1_000_000_000, 1_250_000_000, 1_500_000_000, 2_000_000_000,
]
SIZE_QUANTUM = 100_000

COUPON_FREQUENCIES = ["Monthly", "Quarterly", "Semi-Annual"]
SETTLEMENT_PERIODS = ["T+2", "T+3", "T+5"]

#: Deal-level DEAL_TYPE (§20.3.2) — the four values the tab offers. Each is also a
#: PRODUCT_GROUP value in asset_types.csv, so the chosen deal type constrains the
#: internal asset-type pick (and therefore INDUSTRY / POOL_PROFILE / PRICING_SPEED /
#: USER_OF_PROCEEDS / the Series PRODUCT_GROUP) to the matching rows.
DEAL_TYPES = ("ABS", "CLO", "CMBS", "RMBS")

#: DEAL_TYPE -> the ISSUER_SECTOR values valid for it (§20.3.2). ISSUER_SECTOR is
#: drawn from this list once per deal. Every deal type has at least one sector, so
#: ISSUER_SECTOR is always populated. "Freddie K" is deliberately shared by RMBS and
#: CMBS (agency multifamily).
DEAL_TYPE_SECTORS: Dict[str, List[str]] = {
    "ABS": ["Aircraft", "Autos", "Cards", "Consumer Loans", "Data Centers",
            "Equipment", "Student Loan", "Timeshares", "Utility", "Whole Biz"],
    "CMBS": ["Conduit", "SASB", "Freddie K"],
    "RMBS": ["CRT", "Non-QM", "Prime 2.0", "RPL", "Freddie K"],
    "CLO": ["CRE CLO"],
}

#: ISSUER_SECTOR -> the SUB_INDUSTRY values nested under it (§20.3.2). A sector absent
#: here (Aircraft, Conduit, CRT, Data Centers, Freddie K, Non-QM, Prime 2.0, RPL, SASB,
#: Timeshares, Utility, Whole Biz) emits **no** SUB_INDUSTRY — the field is omitted, not
#: sent empty. The "3.0 *" CLO tranche types are nested under the CLO sector "CRE CLO".
SECTOR_SUB_INDUSTRY: Dict[str, List[str]] = {
    "Autos": ["Auto Leases", "Auto Prime Loans", "Auto Subprime Loans",
              "Motorcycle Prime Loans", "Rental Car", "Fleet Lease", "Dealer Floorplan"],
    "Cards": ["Bank Cards", "Retail Cards"],
    "Consumer Loans": ["Mobile Phone"],
    "Equipment": ["Equipment Loans/Leases"],
    "Student Loan": ["FFELP", "PSL"],
    "CRE CLO": ["3.0 Double-A", "3.0 Duper A1/A2", "3.0 Duper LCF",
                "3.0 Junior AAA", "3.0 Single-A"],
}

#: §20.3.6 — the numeric-looking fields the schema wants as JSON **strings**. Everything
#: else numeric goes over the wire as a JSON number. One set, coerced at serialisation.
_STRING_FIELDS = frozenset({
    "ISSUER_CIK", "ORIGINATOR_CIK", "SPONSOR_CIK",
    "PERCENT_CA", "WALA", "WAL", "INTEREST_DUE", "IPO_PRICE",
})

#: §20.3.6 — the other side of the same table, asserted by ``check_payload``.
_NUMBER_FIELDS = frozenset({
    "DEAL_SIZE", "SERIES_SIZE", "POOL_SIZE", "TRANCHE_SIZE",
    "AOLS", "FICO_SCORE", "LTV", "WA_COUPON", "WA_MATURITY_MONTHS",
    "INITIAL_NOTE_BALANCE", "NET_PROCEEDS",
    "INT_RATE", "YIELD", "SPREAD_BPS", "CREDIT_ENHANCEMENT_PCT",
    "UNDERWRITING_DISC_AND_COMMISIONS", "MINIMUM_PIECE", "MINIMUM_INCREMENT",
    "ANNOUNCEMENT_DT", "EXPECTED_PRICING_DATE", "SETTLEMENT_DATE", "FIRST_COUPON_DT",
    "MATURITY_DATE", "TRANCHE_SETTLEMENT_DATE", "ANT_REDEMPTION_DATE",
})

#: §20.3.7 — checked present and non-empty on every node before posting.
MANDATORY = {
    "deal": ("ORIGINATOR", "ISSUER_NAME", "ISSUER_TICKER", "ISSUER_CIK", "CURRENCY_CODE",
             "DEAL_TYPE", "INDUSTRY"),
    "series": ("SERIES_NAME", "CURRENCY_CODE", "MATURITY_DATE"),
    "tranche": ("TRANCHE_CLASS", "CCY", "TENOR", "COUPON_TYPE", "MATURITY_DATE"),
    "security": ("REG_TYPE",),
}

#: §20.6.4 — RANKING and TRANCHE_DESCRIPTION stems, by credit-class depth. All four
#: RANKING values are members of the 28-value vocabulary (§20.5.5).
CLASS_RANKING = ["Senior Secured", "Secured", "Subordinated", "Junior Subordinated"]
CLASS_DESCRIPTION = ["Senior", "Mezzanine", "Junior mezzanine", "Subordinate"]

#: §20.5.3 — which pool statistics apply, and their plausible ranges, per POOL_PROFILE.
POOL_PROFILES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "consumer_auto": {"FICO_SCORE": (620, 800), "LTV": (85, 110), "AOLS": (18_000, 42_000),
                      "WA_COUPON": (5, 12), "WA_MATURITY_MONTHS": (48, 75),
                      "WALA": (4, 20), "PERCENT_CA": (8, 24)},
    "consumer_card": {"FICO_SCORE": (660, 780), "WA_COUPON": (12, 22), "PERCENT_CA": (8, 20)},
    "mortgage": {"FICO_SCORE": (640, 790), "LTV": (60, 90), "WALA": (6, 60),
                 "WA_COUPON": (3, 8), "WA_MATURITY_MONTHS": (240, 360)},
    "commercial": {"LTV": (50, 75), "WA_COUPON": (4, 9), "WA_MATURITY_MONTHS": (60, 120)},
    "corporate_credit": {"WA_COUPON": (6, 11), "WA_MATURITY_MONTHS": (48, 96)},
    "esoteric": {"WA_COUPON": (5, 10), "WA_MATURITY_MONTHS": (60, 180)},
}

#: asset_types.csv encodes "no prepayment convention applies" as this pair. Emitting the
#: literal string "None" would be plausible-but-wrong, so both fields are omitted instead.
NO_PREPAYMENT = "None"

# ── reference-data fallbacks (§20.5.1 — every file is optional) ────────────────

ENTITIES_FALLBACK = [{
    "ISSUER_NAME": "Oakhurst Auto Receivables Trust", "ISSUER_TICKER": "OART",
    "ISSUER_CIK": "1701472", "ORIGINATOR": "Oakhurst Auto Finance LLC",
    "ORIGINATOR_TICKER": "OAKAF", "ORIGINATOR_CIK": "1701455",
    "SPONSOR_NAME": "Oakhurst Capital Markets LLC", "SPONSOR_TICKER": "OAKCM",
    "SPONSOR_CIK": "1701460", "DEPOSITOR": "Oakhurst Auto Receivables Depositor LLC",
    "SERVICER": "Oakhurst Auto Finance LLC", "ASSET_FAMILY": "auto",
}]
ASSET_TYPES_FALLBACK = [{
    "ASSET_TYPE": "Auto Loan ABS", "PRODUCT_GROUP": "ABS", "INDUSTRY": "Consumer Finance",
    "SUB_INDUSTRY": "Auto Loans", "BUSINESS": "Prime retail auto installment loan receivables",
    "PREPAYMENT_TYPE": "ABS", "PRICING_SPEED": "1.30% ABS", "POOL_PROFILE": "consumer_auto",
    "USER_OF_PROCEEDS": "Purchase auto receivables from the depositor and fund the reserve account",
    "ASSET_FAMILY": "auto",
}]
CURRENCIES_FALLBACK = [{
    "CODE": "USD", "COUNTRY": "US", "COUNTRY_NAME": "United States", "FLOAT_BENCHMARK": "SOFR",
    "INDEX_LEVEL": "4.33", "CLEARING_SYSTEM": "DTC|Euroclear",
    "DEPOSITORY": "DTC|Euroclear / Clearstream", "ISIN_PREFIX": "US", "GOVERNING_LAW": "NY Law",
    "LISTING_EXCHANGE": "Unlisted", "DEFAULT_REG_TYPES": "144A|Reg S", "DAY_COUNT": "30/360",
}]
AGENTS_FALLBACK = [{"NAME": "Citibank, N.A.",
                    "ROLES": "trustee|paying_agent|registrar|calculation_agent|custodian|backup_servicer"}]
UNDERWRITERS_FALLBACK = [{"NAME": "J.P. Morgan Securities LLC", "TICKER": "JPM"},
                         {"NAME": "BofA Securities, Inc.", "TICKER": "BAC"},
                         {"NAME": "Citigroup Global Markets Inc.", "TICKER": "C"},
                         {"NAME": "Wells Fargo Securities, LLC", "TICKER": "WFC"}]
LEGAL_ADVISORS_FALLBACK = [{"NAME": "Mayer Brown LLP"}, {"NAME": "Sidley Austin LLP"}]
AUDITORS_FALLBACK = [{"NAME": "Deloitte & Touche LLP"}]
RATINGS_FALLBACK = [
    {"RANK": "1", "FITCH": "AAA", "MOODYS": "Aaa", "SP": "AAA"},
    {"RANK": "2", "FITCH": "AA+", "MOODYS": "Aa1", "SP": "AA+"},
    {"RANK": "3", "FITCH": "AA", "MOODYS": "Aa2", "SP": "AA"},
    {"RANK": "4", "FITCH": "AA-", "MOODYS": "Aa3", "SP": "AA-"},
    {"RANK": "5", "FITCH": "A+", "MOODYS": "A1", "SP": "A+"},
    {"RANK": "6", "FITCH": "A", "MOODYS": "A2", "SP": "A"},
    {"RANK": "7", "FITCH": "A-", "MOODYS": "A3", "SP": "A-"},
    {"RANK": "8", "FITCH": "BBB+", "MOODYS": "Baa1", "SP": "BBB+"},
    {"RANK": "9", "FITCH": "BBB", "MOODYS": "Baa2", "SP": "BBB"},
    {"RANK": "10", "FITCH": "BBB-", "MOODYS": "Baa3", "SP": "BBB-"},
    {"RANK": "11", "FITCH": "BB+", "MOODYS": "Ba1", "SP": "BB+"},
    {"RANK": "12", "FITCH": "BB", "MOODYS": "Ba2", "SP": "BB"},
    {"RANK": "13", "FITCH": "BB-", "MOODYS": "Ba3", "SP": "BB-"},
    {"RANK": "14", "FITCH": "B+", "MOODYS": "B1", "SP": "B+"},
]
ANALYSTS_FALLBACK = [{"EMAIL": "abs.analyst@troweprice.com"}]
VOCAB_FALLBACK = [
    {"FIELD": "ACCRUAL_METHOD", "VALUE": "Actual balance accrual", "WEIGHT": "6"},
    {"FIELD": "BUSINESS_DAY_CONVENTION", "VALUE": "Following", "WEIGHT": "6"},
    {"FIELD": "COUPON_TYPE", "VALUE": "Fixed", "WEIGHT": "6"},
    {"FIELD": "COUPON_TYPE", "VALUE": "Float", "WEIGHT": "4"},
    {"FIELD": "DAY_COUNT", "VALUE": "30/360", "WEIGHT": "5"},
    {"FIELD": "RANKING", "VALUE": "Senior Secured", "WEIGHT": "8"},
]


# ── reference data ─────────────────────────────────────────────────────────────

class ReferenceData:
    """Every CSV under ``reference/securitized`` — all optional, all with a fallback."""

    def __init__(self, ref_dir: str) -> None:
        self.dir: Optional[Path] = Path(ref_dir) if ref_dir else None
        self.missing: List[str] = []

        self.entities = self._rows("entities.csv", ENTITIES_FALLBACK)
        self.asset_types = self._rows("asset_types.csv", ASSET_TYPES_FALLBACK)
        self.agents_rows = self._rows("agents.csv", AGENTS_FALLBACK)
        self.underwriters = self._rows("underwriters.csv", UNDERWRITERS_FALLBACK)
        self.legal_advisors = [r["NAME"] for r in self._rows("legal_advisors.csv", LEGAL_ADVISORS_FALLBACK)]
        self.auditors = [r["NAME"] for r in self._rows("auditors.csv", AUDITORS_FALLBACK)]
        self.analysts = [r["EMAIL"] for r in self._rows("analysts.csv", ANALYSTS_FALLBACK)]

        self.ratings = sorted(self._rows("ratings.csv", RATINGS_FALLBACK),
                              key=lambda r: int(r["RANK"]))

        self.vocab: Dict[str, List[Tuple[str, float]]] = {}
        for row in self._rows("vocabularies.csv", VOCAB_FALLBACK):
            try:
                weight = float(row.get("WEIGHT") or 0)
            except ValueError:
                weight = 0.0
            self.vocab.setdefault(row["FIELD"], []).append((row["VALUE"], weight))

        self.currencies: Dict[str, Dict[str, Any]] = {}
        for row in self._rows("currencies.csv", CURRENCIES_FALLBACK):
            self.currencies[row["CODE"].upper()] = self._parse_currency(row)

        self.agents: Dict[str, List[str]] = {}
        for row in self.agents_rows:
            for role in (row.get("ROLES") or "").split("|"):
                if role:
                    self.agents.setdefault(role, []).append(row["NAME"])

        # §20.5.2 — the family is the join key between entities and asset types.
        self.types_by_family: Dict[str, List[Dict]] = {}
        for row in self.asset_types:
            self.types_by_family.setdefault(row.get("ASSET_FAMILY", ""), []).append(row)

    # -- loading ---------------------------------------------------------------

    def _rows(self, name: str, fallback: List[Dict]) -> List[Dict]:
        path = (self.dir / name) if self.dir else None
        if not path or not path.exists():
            self.missing.append(name)
            return [dict(r) for r in fallback]
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]
        if not rows:
            self.missing.append(name)
            return [dict(r) for r in fallback]
        return rows

    @staticmethod
    def _parse_currency(row: Dict[str, str]) -> Dict[str, Any]:
        def split(key: str) -> List[str]:
            return [p for p in (row.get(key) or "").split("|") if p]

        levels = []
        for raw in split("INDEX_LEVEL"):
            try:
                levels.append(float(raw))
            except ValueError:
                levels.append(4.0)
        return {
            "CODE": row["CODE"].upper(),
            "COUNTRY": split("COUNTRY") or ["US"],
            "COUNTRY_NAME": split("COUNTRY_NAME") or ["United States"],
            "FLOAT_BENCHMARK": split("FLOAT_BENCHMARK") or ["SOFR"],
            "INDEX_LEVEL": levels or [4.0],
            "CLEARING_SYSTEM": split("CLEARING_SYSTEM") or ["Euroclear"],
            "DEPOSITORY": split("DEPOSITORY") or ["Euroclear / Clearstream"],
            "ISIN_PREFIX": (row.get("ISIN_PREFIX") or "XS").strip(),
            "GOVERNING_LAW": split("GOVERNING_LAW") or ["English Law"],
            "LISTING_EXCHANGE": split("LISTING_EXCHANGE") or ["Unlisted"],
            "DEFAULT_REG_TYPES": split("DEFAULT_REG_TYPES") or ["Reg S"],
            "DAY_COUNT": (row.get("DAY_COUNT") or "30/360").strip(),
        }

    # -- lookups ---------------------------------------------------------------

    def vocab_values(self, field: str) -> List[str]:
        return [v for v, _ in self.vocab.get(field, [])]

    def pick_vocab(self, field: str, default: str = "") -> str:
        """Weighted draw from a controlled vocabulary (§20.5.5); zero-weight rows never win."""
        entries = [(v, w) for v, w in self.vocab.get(field, []) if w > 0]
        if not entries:
            entries = [(v, 1.0) for v, _ in self.vocab.get(field, [])]
        if not entries:
            return default
        values, weights = zip(*entries)
        return random.choices(values, weights=weights, k=1)[0]

    def agent(self, role: str) -> str:
        names = self.agents.get(role) or [r["NAME"] for r in self.agents_rows]
        return random.choice(names)

    def rating_triplet(self, rank: int) -> Tuple[str, str, str]:
        idx = max(0, min(rank - 1, len(self.ratings) - 1))
        row = self.ratings[idx]
        return row["FITCH"], row["MOODYS"], row["SP"]


# ── small helpers ──────────────────────────────────────────────────────────────

def _as_int_list(value: Any, default: List[int]) -> List[int]:
    """`3` / `"1, 2, 3"` / `[1,2]` -> `[…]`, per the cycling-list convention (§20.9)."""
    if value is None or value == "":
        return list(default)
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        out = [int(p) for p in value.replace(";", ",").split(",") if p.strip()]
        return out or list(default)
    out = [int(v) for v in value]
    return out or list(default)


def _split_exact(total: int, weights: Sequence[float], senior_index: int = 0,
                 quantum: int = SIZE_QUANTUM) -> List[int]:
    """Split ``total`` on ``weights``, rounded to ``quantum``, remainder to the senior part.

    §20.6.1 — the sum is exact by construction, so the roll-up never drifts.
    """
    if not weights:
        return []
    if len(weights) == 1:
        return [total]
    tot = float(sum(weights)) or 1.0
    parts = [int(round(total * w / tot / quantum)) * quantum for w in weights]
    # Nothing may round to zero: borrow from the senior part, which absorbs the remainder.
    for i, part in enumerate(parts):
        if part < quantum:
            parts[i] = quantum
    parts[senior_index] += total - sum(parts)
    return parts


def _class_layout(n: int) -> List[str]:
    """§20.6.4 — TRANCHE_CLASS sequence for ``n`` tranches; A splits first."""
    table = {
        1: ["A"],
        2: ["A", "B"],
        3: ["A", "B", "C"],
        4: ["A-1", "A-2", "B", "C"],
        5: ["A-1", "A-2", "A-3", "B", "C"],
        6: ["A-1", "A-2", "A-3", "B", "C", "D"],
        7: ["A-1", "A-2", "A-3", "A-4", "B", "C", "D"],
    }
    if n in table:
        return table[n]
    if n < 1:
        return ["A"]
    # Beyond the realistic range (allowed, not clamped — §20.9): keep the 4-piece senior
    # stack and extend the subordinate letters E, F, G, …
    senior = ["A-1", "A-2", "A-3", "A-4"]
    subs = [chr(ord("B") + i) for i in range(n - len(senior))]
    return senior + subs


def credit_class(tranche_class: str) -> str:
    """§20.6.3 — the letter before the dash. ``A-1``/``A-2``/``A-3`` are all class ``A``."""
    return tranche_class.split("-", 1)[0]


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if stop_event.is_set():
            return False
        time.sleep(min(0.1, max(0.0, end - time.monotonic())))
    return True


_PRICING_SPEED_RE = re.compile(r"^\s*([\d.]+)\s*%\s*(\S+)\s*$")


def _vary_pricing_speed(quote: str) -> str:
    """``"1.30% ABS"`` -> ``"1.24% ABS"`` — vary the number, keep the convention suffix.

    ``asset_types.csv`` holds one representative quote per asset type; slice 1 left the
    variation to the engine so two deals of the same asset type do not price identically.
    """
    m = _PRICING_SPEED_RE.match(quote or "")
    if not m:
        return quote
    raw, suffix = m.group(1), m.group(2)
    places = len(raw.split(".", 1)[1]) if "." in raw else 0
    value = float(raw) * random.uniform(0.8, 1.2)
    return f"{value:.{places}f}% {suffix}"


def _num(value: float, places: int = 2) -> float:
    """Round, and drop a trailing ``.0`` so whole amounts serialise as ints."""
    out = round(float(value), places)
    return int(out) if out == int(out) else out


# ── generation ─────────────────────────────────────────────────────────────────

class DealGenerator:
    """Builds the deal tree as nested lists; ``_serialise`` turns it into the map form."""

    def __init__(self, ref: ReferenceData, pool: IdentifierPool,
                 coupon_type: str = "Fixed", forced_currency: str = "",
                 forced_deal_type: str = "") -> None:
        self.ref = ref
        self.pool = pool
        self.coupon_type = coupon_type
        self.forced_currency = (forced_currency or "").upper()
        self.forced_deal_type = (forced_deal_type or "").upper()

    # -- picks -----------------------------------------------------------------

    def _pick_currency(self) -> Dict[str, Any]:
        if self.forced_currency and self.forced_currency in self.ref.currencies:
            return self.ref.currencies[self.forced_currency]
        return random.choice(list(self.ref.currencies.values()))

    def _pick_deal(self) -> Tuple[Dict, Dict, str]:
        """§20.3.2 — DEAL_TYPE drives the asset pick, which drives the entity.

        The chosen (or random) DEAL_TYPE constrains the asset type to the matching
        ``PRODUCT_GROUP`` rows; the asset's ``ASSET_FAMILY`` then constrains the entity
        (§20.5.2). ABS has 51 candidate asset types; CLO/CMBS/RMBS have one each, so
        those deal types resolve to a fixed asset profile.
        """
        deal_type = self.forced_deal_type or random.choice(DEAL_TYPES)
        candidates = [t for t in self.ref.asset_types if t.get("PRODUCT_GROUP") == deal_type]
        asset = random.choice(candidates or self.ref.asset_types)
        family = asset.get("ASSET_FAMILY", "")
        entities = [e for e in self.ref.entities if e.get("ASSET_FAMILY") == family]
        entity = random.choice(entities or self.ref.entities)
        return entity, asset, deal_type

    def _pick_sector_and_sub(self, deal_type: str) -> Tuple[str, Optional[str]]:
        """§20.3.2 — ISSUER_SECTOR from the deal type, then SUB_INDUSTRY nested under it.

        A sector with no nested sub-industries returns ``None`` for SUB_INDUSTRY, and
        the caller omits the field rather than sending it empty.
        """
        sectors = DEAL_TYPE_SECTORS.get(deal_type) or []
        sector = random.choice(sectors) if sectors else ""
        subs = SECTOR_SUB_INDUSTRY.get(sector) or []
        sub_industry = random.choice(subs) if subs else None
        return sector, sub_industry

    def _coupon_for(self, index: int) -> str:
        if self.coupon_type == "Mixed":
            return random.choice(["Fixed", "Float"])
        return self.coupon_type

    # -- ladders ---------------------------------------------------------------

    @staticmethod
    def _int_rate_ladder(counts: List[int]) -> List[List[float]]:
        """Fixed coupon per tranche: rises across classes, small term premium within one."""
        out: List[List[float]] = []
        base = round(random.uniform(3.5, 5.0), 2)
        for ci, count in enumerate(counts):
            step = round(random.uniform(0.5, 1.5), 2)
            drift = round(random.uniform(0.05, 0.20), 2)
            # The class-boundary jump must clear the within-class drift (§20.6.4).
            span = drift * max(0, count - 1)
            if ci + 1 < len(counts):
                step = max(step, span + 0.10)
            out.append([round(base + drift * p, 2) for p in range(count)])
            base = round(base + step, 2)
        return out

    @staticmethod
    def _fixed_spread_ladder(counts: List[int]) -> List[List[int]]:
        """§20.4.3 — fine granularity; ×2–×3 per class, a few bp of term premium within one."""
        out: List[List[int]] = []
        base = random.randint(15, 40)
        for ci, count in enumerate(counts):
            drift = random.randint(2, 8)
            values = [base + drift * p for p in range(count)]
            out.append(values)
            nxt = int(round(base * random.uniform(2.0, 3.0)))
            base = max(nxt, values[-1] + 10)
        return out

    @staticmethod
    def _float_spread_ladder(counts: List[int]) -> List[List[int]]:
        """§20.4.3 — multiples of 10 throughout: senior starts at 20, +10 per senior step."""
        out: List[List[int]] = []
        base = 20
        for ci, count in enumerate(counts):
            values = [base + 10 * p for p in range(count)]
            out.append(values)
            step = random.choice([10, 20, 30, 40])
            base = values[-1] + max(step, 10)
        return out

    @staticmethod
    def _discount_ladder(counts: List[int]) -> List[List[float]]:
        """UNDERWRITING_DISC_AND_COMMISIONS: rises across classes (§20.6.4)."""
        out: List[List[float]] = []
        base = round(random.uniform(0.25, 0.35), 3)
        for ci, count in enumerate(counts):
            drift = 0.005
            values = [round(base + drift * p, 3) for p in range(count)]
            out.append(values)
            base = round(values[-1] + random.uniform(0.05, 0.15), 3)
        return out

    # -- pool statistics -------------------------------------------------------

    def _pool_stats(self, profile: str, currency: str) -> Dict[str, Any]:
        """§20.5.3 — only the statistics the profile names; PERCENT_CA is US-only."""
        spec = POOL_PROFILES.get(profile, POOL_PROFILES["esoteric"])
        stats: Dict[str, Any] = {}
        for field, (low, high) in spec.items():
            if field == "PERCENT_CA":
                if currency != "USD":
                    continue
                stats["PERCENT_CA"] = str(random.randint(int(low), int(high)))
            elif field == "WALA":
                stats["WALA"] = f"{random.randint(int(low), int(high))} Months"
            elif field in ("FICO_SCORE", "AOLS", "WA_MATURITY_MONTHS"):
                stats[field] = random.randint(int(low), int(high))
            else:
                stats[field] = _num(random.uniform(low, high), 2)
        return stats

    # -- the tree --------------------------------------------------------------

    def generate_deal(self, series_count: int, tranche_counts: List[int],
                      security_counts: List[int]) -> Dict[str, Any]:
        """One deal: ``{"fields": {...}, "series": [...]}`` — see ``_serialise``."""
        ccy = self._pick_currency()
        currency = ccy["CODE"]
        entity, asset, deal_type = self._pick_deal()
        issuer_sector, sub_industry = self._pick_sector_and_sub(deal_type)

        bench_idx = random.randrange(len(ccy["FLOAT_BENCHMARK"]))
        benchmark = ccy["FLOAT_BENCHMARK"][bench_idx]
        index_level = ccy["INDEX_LEVEL"][min(bench_idx, len(ccy["INDEX_LEVEL"]) - 1)]

        cusip_base = self.pool.new_cusip_base()

        # §20.7 — series 1 is announced a few days out; series n+1 follows 1–4 weeks later.
        first_announcement = D._roll_to_weekday(date.today() + timedelta(days=random.randint(3, 10)))

        deal_size = random.choice(DEAL_SIZE_LADDER)
        series_sizes = _split_exact(deal_size, [random.uniform(0.8, 1.2) for _ in range(series_count)])

        vintage_start = random.randint(1, 4)
        year = first_announcement.year

        underwriters = [u["NAME"] for u in self.ref.underwriters]
        n_book = min(random.randint(2, 4), len(underwriters))
        bookrunners = random.sample(underwriters, k=n_book)
        rest = [u for u in underwriters if u not in bookrunners]
        n_co = min(random.randint(1, 3), len(rest))
        co_managers = random.sample(rest, k=n_co) if n_co else []

        series_list: List[Dict[str, Any]] = []
        announcement = first_announcement
        tranche_ordinal = 0          # deal-wide: CUSIP suffixes advance once per tranche (§20.8.1)
        reg_types_used: set = set()

        for s_idx in range(series_count):
            n_tranches = tranche_counts[s_idx]
            series, tranche_ordinal = self._generate_series(
                s_idx=s_idx, vintage=f"{year}-{vintage_start + s_idx}",
                announcement=announcement, size=series_sizes[s_idx],
                n_tranches=n_tranches,
                security_counts=security_counts,
                entity=entity, asset=asset, ccy=ccy, benchmark=benchmark,
                index_level=index_level, cusip_base=cusip_base,
                tranche_ordinal=tranche_ordinal, reg_types_used=reg_types_used,
            )
            series_list.append(series)
            announcement = D.next_series_announcement(announcement)

        # §20.6.3 — the providers string names the features actually used. Subordination
        # only exists if some series has more than one credit class.
        has_subordination = any(
            len({credit_class(t["fields"]["TRANCHE_CLASS"]) for t in s["tranches"]}) > 1
            for s in series_list
        )
        features = ["Overcollateralization"]
        if has_subordination:
            features.append("subordination")
        features.append("reserve account")
        providers = " + ".join(features)

        origin_rank = random.randint(5, 16)
        o_fitch, o_moody, o_sp = self.ref.rating_triplet(origin_rank)
        country = random.choice(ccy["COUNTRY"])
        is_roadshow = random.random() < 0.2

        fields = {
            "ANNOUNCEMENT_DT": D.to_epoch_ms(first_announcement),
            "AUDITORS": random.choice(self.ref.auditors),
            "BACKUP_SERVICER": self.ref.agent("backup_servicer"),
            "BOOKRUNNERS": " / ".join(bookrunners),
            "BUSINESS": asset.get("BUSINESS", ""),
            "CALCULATION_AGENT": self.ref.agent("calculation_agent"),
            "COUNTRY": country,
            "COVENANT": DEAL_COVENANT,
            "CO_MANAGERS": " / ".join(co_managers),
            "CREDIT_ENHANCEMENT_PROVIDERS": providers,
            "CURRENCY_CODE": currency,
            "CUSTODIAN": self.ref.agent("custodian"),
            "DEAL_SIZE": sum(series_sizes),
            "DEAL_TYPE": deal_type,
            "DEPOSITOR": entity.get("DEPOSITOR", ""),
            # §20.3.2 — the issuer sector is carried in INDUSTRY: the schema has no
            # ISSUER_SECTOR property (closed schema NACKs it, verified live 2026-09-09),
            # so the sector value reuses the existing, schema-valid INDUSTRY field.
            "INDUSTRY": issuer_sector,
            "INVESTOR_STATUS": INVESTOR_STATUS,
            "ISSUER_CIK": entity.get("ISSUER_CIK", ""),
            "ISSUER_NAME": f"{entity.get('ISSUER_NAME', '')} {year}-{vintage_start}",
            "ISSUER_TICKER": f"{entity.get('ISSUER_TICKER', '')}{year % 100}{vintage_start}",
            "IS_ROADSHOW": is_roadshow,
            "LEGAL_ADVISORS": self._legal_advisors(),
            "LIQUIDITY_FACILITY_PROVIDER": (
                "None (reserve account funded)" if random.random() < 0.7
                else random.choice(underwriters)
            ),
            "ORIGINATOR": entity.get("ORIGINATOR", ""),
            "ORIGINATOR_CIK": entity.get("ORIGINATOR_CIK", ""),
            "ORIGINATOR_FITCH_RATING": o_fitch,
            "ORIGINATOR_MOODY_RATING": o_moody,      # singular MOODY — schema spelling (§20.3.2)
            "ORIGINATOR_SANDP_RATING": o_sp,         # SANDP, not SP
            "ORIGINATOR_TICKER": entity.get("ORIGINATOR_TICKER", ""),
            "PAYING_AGENT": self.ref.agent("paying_agent"),
            "RATING_AGENCIES": RATING_AGENCIES,
            "REGISTRAR": self.ref.agent("registrar"),
            "SERVICER": entity.get("SERVICER", ""),
            "SPONSOR_CIK": entity.get("SPONSOR_CIK", ""),
            "SPONSOR_NAME": entity.get("SPONSOR_NAME", ""),
            "SPONSOR_TICKER": entity.get("SPONSOR_TICKER", ""),
            "TRADE_DESK": TRADE_DESK,
            "TRUSTEE": self.ref.agent("trustee"),
            "UNDERWRITER": bookrunners[0],
            "USER_OF_PROCEEDS": asset.get("USER_OF_PROCEEDS", ""),   # USER_, not USE_ (§20.3.2)
        }

        # §20.3.2 — SUB_INDUSTRY is nested under the issuer sector (carried in INDUSTRY):
        # a sector with no nested values (e.g. Aircraft, Conduit, Data Centers) emits no
        # SUB_INDUSTRY at all.
        if sub_industry:
            fields["SUB_INDUSTRY"] = sub_industry

        # §20.3.8 — MANDATE_TEXT is conditionally mandatory: a roadshow deal
        # without it NACKs "Mandate Text is required". Verified live 2026-08-13
        # (slice 3, step 7.2); it is accepted but not required when IS_ROADSHOW
        # is false, so it is omitted there rather than sent empty.
        if is_roadshow:
            fields["MANDATE_TEXT"] = (
                f"{bookrunners[0]} mandated as lead bookrunner on the "
                f"{fields['ISSUER_NAME']} {asset.get('ASSET_TYPE', '')} transaction; "
                f"investor roadshow to follow, pricing expected thereafter."
            )

        return {"fields": fields, "series": series_list,
                "meta": {"deal_type": deal_type, "asset_type": asset.get("ASSET_TYPE", ""),
                         "currency": currency}}

    def _legal_advisors(self) -> str:
        if len(self.ref.legal_advisors) >= 2:
            a, b = random.sample(self.ref.legal_advisors, k=2)
        else:
            a = b = self.ref.legal_advisors[0]
        return f"{a} (issuer) / {b} (underwriters)"

    def _generate_series(self, *, s_idx: int, vintage: str, announcement: date, size: int,
                         n_tranches: int, security_counts: List[int], entity: Dict, asset: Dict,
                         ccy: Dict, benchmark: str, index_level: float, cusip_base: str,
                         tranche_ordinal: int, reg_types_used: set) -> Tuple[Dict, int]:
        currency = ccy["CODE"]
        dates = D.series_dates(announcement)

        classes = _class_layout(n_tranches)
        credit_classes: List[str] = []
        for cls in classes:
            cc = credit_class(cls)
            if cc not in credit_classes:
                credit_classes.append(cc)
        counts = [sum(1 for c in classes if credit_class(c) == cc) for cc in credit_classes]

        # ── sizes: class first, then within the class (§20.6.1) ──────────────
        if len(credit_classes) == 1:
            class_weights = [1.0]
        else:
            senior = random.uniform(0.80, 0.88)
            tail = [0.45 ** i for i in range(len(credit_classes) - 1)]
            tail_total = sum(tail)
            class_weights = [senior] + [(1 - senior) * t / tail_total for t in tail]
        class_sizes = _split_exact(size, class_weights)

        tranche_sizes: List[int] = []
        for cc_idx, count in enumerate(counts):
            within = _split_exact(class_sizes[cc_idx], [random.uniform(0.8, 1.2) for _ in range(count)])
            tranche_sizes.extend(within)

        # ── credit enhancement: per CREDIT CLASS, not per tranche (§20.6.3) ──
        # DEVIATION §20.6.3: the reserve drawn for the most junior class (0.5–2.0) is
        # independent of the classes above it, and on a deep stack (5+ classes) the
        # second-most-junior class's subordination can fall *below* it — which breaks the
        # "CE falls monotonically across classes" rule in the same section. The reserve is
        # therefore capped at half the thinnest computed CE.
        reserve_pct = _num(random.uniform(0.5, 2.0), 2)
        ce_by_class: Dict[str, float] = {}
        computed: List[float] = []
        for cc_idx, cc in enumerate(credit_classes[:-1]):
            junior = sum(class_sizes[cc_idx + 1:])
            value = _num(100.0 * junior / size, 2)
            computed.append(value)
            ce_by_class[cc] = value
        if computed:
            reserve_pct = _num(min(reserve_pct, max(0.05, min(computed) / 2)), 2)
        ce_by_class[credit_classes[-1]] = reserve_pct

        # ── ratings, ranking, pricing ladders — one entry per credit class ───
        rank = 1
        ratings_by_class: Dict[str, Tuple[str, str, str]] = {}
        for cc in credit_classes:
            ratings_by_class[cc] = self.ref.rating_triplet(rank)
            rank += random.randint(2, 5)
        ranking_by_class = {cc: CLASS_RANKING[min(i, len(CLASS_RANKING) - 1)]
                            for i, cc in enumerate(credit_classes)}
        desc_by_class = {cc: CLASS_DESCRIPTION[min(i, len(CLASS_DESCRIPTION) - 1)]
                         for i, cc in enumerate(credit_classes)}

        int_rates = self._int_rate_ladder(counts)
        fixed_spreads = self._fixed_spread_ladder(counts)
        float_spreads = self._float_spread_ladder(counts)
        discounts = self._discount_ladder(counts)

        # ── tenors and WALs, strictly ascending down the stack (§20.7.2) ─────
        tenors = self._tenor_ladder(n_tranches)
        wals: List[float] = []
        previous = 0.0
        for tenor in tenors:
            wal = round(tenor * random.uniform(0.45, 0.85), 2)
            if wal <= previous:
                wal = round((previous + tenor) / 2, 2)
            if wal >= tenor:
                wal = round(tenor - 0.25, 2)
            wals.append(wal)
            previous = wal

        analyst = random.choice(self.ref.analysts)
        prepayment = asset.get("PREPAYMENT_TYPE", "")
        # Shared across the series' tranches — §20.6.4 lists PRICING_SPEED among the
        # fields the senior stack holds in common.
        pricing_speed = _vary_pricing_speed(asset.get("PRICING_SPEED", ""))

        tranches: List[Dict[str, Any]] = []
        position = {cc: 0 for cc in credit_classes}
        maturities: List[date] = []

        for t_idx, cls in enumerate(classes):
            cc = credit_class(cls)
            cc_idx = credit_classes.index(cc)
            p = position[cc]
            position[cc] += 1

            coupon = self._coupon_for(t_idx)
            size_t = tranche_sizes[t_idx]
            tenor = tenors[t_idx]
            wal = wals[t_idx]
            discount = discounts[cc_idx][p]
            fitch, moodys, sp = ratings_by_class[cc]

            settlement = D.tranche_settlement(dates["settlement"], t_idx)
            maturity = D.tranche_maturity(announcement, tenor)
            redemption = D.ant_redemption_date(settlement, wal, maturity)
            maturities.append(maturity)

            fields: Dict[str, Any] = {
                "TRANCHE_CLASS": cls,
                "TRANCHE_DESCRIPTION": f"{desc_by_class[cc]} "
                                       f"{'fixed' if coupon == 'Fixed' else 'floating'}-rate class",
                "TRANCHE_SIZE": size_t,
                "INITIAL_NOTE_BALANCE": size_t,
                "UNDERWRITING_DISC_AND_COMMISIONS": discount,   # one S — schema spelling
                "NET_PROCEEDS": _num(size_t * (1 - discount / 100.0), 2),
                "CREDIT_ENHANCEMENT_PCT": ce_by_class[cc],
                "CCY": currency,
                "COUPON_TYPE": coupon,
                "IPO_PRICE": IPO_PRICE,
                "PRICE_TYPE": PRICE_TYPE,
                "RANKING": ranking_by_class[cc],
                "TENOR": f"{tenor}Y",
                "WAL": f"{wal:.2f}",
                "MINIMUM_PIECE": MINIMUM_PIECE,
                "MINIMUM_INCREMENT": MINIMUM_INCREMENT,
                "TRANCHE_FITCH": fitch,
                "TRANCHE_MOODYS": moodys,
                "TRANCHE_SP": sp,
                "CREDIT_ANALYST": analyst,
                "MATURITY_DATE": D.to_epoch_ms(maturity),
                "ANT_REDEMPTION_DATE": D.to_epoch_ms(redemption),
                "TRANCHE_SETTLEMENT_DATE": D.to_epoch_ms(settlement),
            }
            # asset_types.csv uses "None"/"0% CPR" for asset types with no prepayment
            # convention; omit rather than emit the literal (deviation, see module docstring).
            if prepayment and prepayment != NO_PREPAYMENT:
                fields["PREPAYMENT_TYPE"] = prepayment
                if pricing_speed:
                    fields["PRICING_SPEED"] = pricing_speed

            if coupon == "Float":
                # §20.4.2 — INT_RATE and YIELD are omitted entirely, not zeroed or nulled.
                spread = float_spreads[cc_idx][p]
                fields["BENCHMARK"] = benchmark
                fields["SPREAD_BPS"] = spread
                fields["CURVE_REFERENCE"] = f"{benchmark} + {spread} bps"
                rate = index_level + spread / 100.0
            else:
                spread = fixed_spreads[cc_idx][p]
                rate = int_rates[cc_idx][p]
                fields["INT_RATE"] = rate
                fields["YIELD"] = rate                       # par re-offer (§20.6.5)
                fields["SPREAD_BPS"] = spread
                fields["CURVE_REFERENCE"] = f"I-Curve + {spread} bps"

            # §20.6.5 — total interest to maturity, integrated over WAL not TENOR.
            fields["INTEREST_DUE"] = str(int(round(size_t * rate / 100.0 * wal)))

            n_sec = security_counts[tranche_ordinal % len(security_counts)]
            securities = self._generate_securities(
                n_sec, cls, ccy, cusip_base, tranche_ordinal, reg_types_used
            )
            tranches.append({"fields": fields, "securities": securities})
            tranche_ordinal += 1

        oc = random.uniform(0.03, 0.08)
        pool_size = int(round(size * (1 + oc) / SIZE_QUANTUM)) * SIZE_QUANTUM
        if pool_size <= size:
            pool_size = size + SIZE_QUANTUM

        audience = self._audience(reg_types_used)
        series_fields: Dict[str, Any] = {
            "SERIES_NAME": f"{entity.get('ISSUER_TICKER', '')} {vintage}",
            "SERIES_SIZE": sum(tranche_sizes),
            "POOL_SIZE": pool_size,
            "PRODUCT_GROUP": asset.get("PRODUCT_GROUP", "ABS"),
            "CURRENCY_CODE": currency,
            "COUPON_FREQUENCY": random.choice(COUPON_FREQUENCIES),
            "DAY_COUNT": ccy["DAY_COUNT"],
            "ACCRUAL_METHOD": self.ref.pick_vocab("ACCRUAL_METHOD", "Actual balance accrual"),
            "BUSINESS_DAY_CONVENTION": self.ref.pick_vocab("BUSINESS_DAY_CONVENTION", "Following"),
            "GOVERNING_LAW": random.choice(ccy["GOVERNING_LAW"]),
            "LISTING_EXCHANGE": random.choice(ccy["LISTING_EXCHANGE"]),
            "REGISTRAR": self.ref.agent("registrar"),
            "SETTLEMENT_PERIOD": random.choice(SETTLEMENT_PERIODS),
            "COVENANT": SERIES_COVENANT,
            "AUDIENCE": audience,
            "EXPECTED_PRICING_DATE": D.to_epoch_ms(dates["pricing"]),
            "SETTLEMENT_DATE": D.to_epoch_ms(dates["settlement"]),
            "FIRST_COUPON_DT": D.to_epoch_ms(dates["first_coupon"]),
            "MATURITY_DATE": D.to_epoch_ms(D.series_legal_final(maturities)),
        }
        series_fields.update(self._pool_stats(asset.get("POOL_PROFILE", "esoteric"), currency))
        return {"fields": series_fields, "tranches": tranches}, tranche_ordinal

    @staticmethod
    def _tenor_ladder(count: int) -> List[int]:
        """§20.7.2, extended past the 8-value pool so a fat-fingered run still generates."""
        if count <= len(D.TENOR_POOL):
            return D.draw_tenor_ladder(count)
        base = list(D.TENOR_POOL)
        extra = 12
        while len(base) < count:
            base.append(extra)
            extra += 3
        return sorted(base[:count])

    @staticmethod
    def _audience(reg_types_used: set) -> str:
        if reg_types_used:
            joined = " / ".join(sorted(reg_types_used))
            return f"Securitized investors ({joined})"
        return "Securitized investors"

    def _generate_securities(self, count: int, tranche_class: str, ccy: Dict,
                             cusip_base: str, tranche_ordinal: int,
                             reg_types_used: set) -> List[Dict[str, Any]]:
        reg_types = ccy["DEFAULT_REG_TYPES"]
        clearing = ccy["CLEARING_SYSTEM"]
        depository = ccy["DEPOSITORY"]
        us_deal = ccy["ISIN_PREFIX"].upper() == "US"

        out: List[Dict[str, Any]] = []
        for j in range(count):
            reg = reg_types[min(j, len(reg_types) - 1)]
            reg_types_used.add(reg)
            fields: Dict[str, Any] = {
                "SECURITY_CLASS": tranche_class,          # == parent TRANCHE_CLASS, always
                "REG_TYPE": reg,
                "FORMAT": SECURITY_FORMAT,
                "SETTLEMENT_TYPE": SETTLEMENT_TYPE,
                "CLEARING_SYSTEM": clearing[min(j, len(clearing) - 1)],
                "DEPOSITORY": depository[min(j, len(depository) - 1)],
            }
            if us_deal and reg == "144A":
                cus = make_cusip(cusip_base, tranche_ordinal)
                fields["CUSIP"] = cus
                fields["ISIN"] = self.pool.claim_isin(isin_144a(cus))
            elif us_deal:
                # Reg S tranche of a US deal: the CINS form, no CUSIP (§20.3.5, §20.8.2).
                fields["ISIN"] = self.pool.claim_isin(isin_regs_us(cusip_base, tranche_ordinal))
            else:
                fields["ISIN"] = self.pool.new_xs_isin()
            if random.random() < 0.30:
                fields["FIGI"] = self.pool.new_figi()
            out.append({"fields": fields})
        return out


# ── serialisation (§20.3.1) ────────────────────────────────────────────────────

def _coerce(key: str, value: Any) -> Any:
    """§20.3.6 — the single place the string-typed numerics are stringified."""
    if value is None:
        return None
    if key in _STRING_FIELDS:
        return str(value)
    return value


def _serialise(deal: Dict[str, Any]) -> Dict[str, Any]:
    """Internal tree -> the ``TEMP-*`` map payload. Ids restart inside each parent."""
    details: Dict[str, Any] = {k: _coerce(k, v) for k, v in deal["fields"].items()}
    series_map: Dict[str, Any] = {}

    for s_idx, series in enumerate(deal["series"]):
        series_id = f"TEMP-Series-{s_idx}"
        s_obj: Dict[str, Any] = {"SERIES_ID": series_id}
        s_obj.update({k: _coerce(k, v) for k, v in series["fields"].items()})
        tranche_map: Dict[str, Any] = {}

        for t_idx, tranche in enumerate(series["tranches"]):
            tranche_id = f"TEMP-Tranches-{t_idx}"          # plural "Tranches" — verbatim
            t_obj: Dict[str, Any] = {"TRANCHE_ID": tranche_id, "SERIES_ID": series_id}
            t_obj.update({k: _coerce(k, v) for k, v in tranche["fields"].items()})
            security_map: Dict[str, Any] = {}

            for c_idx, security in enumerate(tranche["securities"]):
                security_id = f"TEMP-Securities-{c_idx}"
                c_obj: Dict[str, Any] = {"SECURITY_ID": security_id,
                                         "TRANCHE_ID": tranche_id, "SERIES_ID": series_id}
                c_obj.update({k: _coerce(k, v) for k, v in security["fields"].items()})
                security_map[security_id] = c_obj

            t_obj["SECURITIES"] = security_map
            tranche_map[tranche_id] = t_obj

        s_obj["TRANCHES"] = tranche_map
        series_map[series_id] = s_obj

    details["SERIES"] = series_map
    return {"MESSAGE_TYPE": MESSAGE_TYPE, "SERVICE_NAME": SERVICE_NAME, "DETAILS": details}


# ── pre-flight assertions (§20.3.7 + §20.6.6) ──────────────────────────────────

def check_payload(payload: Dict[str, Any]) -> Dict[str, List[str]]:
    """Mandatory-field failures as ``errors``, internal-consistency failures as ``warnings``.

    Runs on the serialised payload — after the map form and the string coercion — so it
    checks what actually goes over the wire.
    """
    errors: List[str] = []
    warnings: List[str] = []
    details = payload.get("DETAILS", {})

    def require(node: Dict[str, Any], level: str, path: str) -> None:
        for field in MANDATORY[level]:
            value = node.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                errors.append(f"{path}.{field} is missing or empty")

    def typecheck(node: Dict[str, Any], path: str) -> None:
        for key, value in node.items():
            if key in ("SERIES", "TRANCHES", "SECURITIES") or value is None:
                continue
            if key in _STRING_FIELDS and not isinstance(value, str):
                warnings.append(f"{path}.{key} must be a string (§20.3.6), got {type(value).__name__}")
            if key in _NUMBER_FIELDS and not isinstance(value, (int, float)):
                warnings.append(f"{path}.{key} must be a number (§20.3.6), got {type(value).__name__}")

    require(details, "deal", "$.DETAILS")
    typecheck(details, "$.DETAILS")

    # §20.3.8 — conditionally mandatory. An error, not a warning: the handler
    # rejects the whole deal with "Mandate Text is required", so catching it here
    # turns a NACK into a named local failure.
    if details.get("IS_ROADSHOW") is True and not str(details.get("MANDATE_TEXT") or "").strip():
        errors.append("$.DETAILS.MANDATE_TEXT is required when IS_ROADSHOW is true (§20.3.8)")

    # §20.3.2 — DEAL_TYPE drives the issuer sector (carried in INDUSTRY), which nests
    # SUB_INDUSTRY. Mismatches are plausible-but-wrong data (like a bad PRODUCT_GROUP),
    # so they warn rather than block.
    deal_type = details.get("DEAL_TYPE")
    sector = details.get("INDUSTRY")
    if deal_type and deal_type not in DEAL_TYPE_SECTORS:
        warnings.append(f"$.DETAILS.DEAL_TYPE {deal_type!r} is not one of {list(DEAL_TYPES)} (§20.3.2)")
    elif deal_type and sector and sector not in DEAL_TYPE_SECTORS.get(deal_type, []):
        warnings.append(f"$.DETAILS.INDUSTRY (issuer sector) {sector!r} is not valid for DEAL_TYPE "
                        f"{deal_type!r} (§20.3.2)")
    sub = details.get("SUB_INDUSTRY")
    if sub and sector and sub not in SECTOR_SUB_INDUSTRY.get(sector, []):
        warnings.append(f"$.DETAILS.SUB_INDUSTRY {sub!r} is not nested under the INDUSTRY "
                        f"(issuer sector) {sector!r} (§20.3.2)")

    deal_ccy = details.get("CURRENCY_CODE")

    series_total = 0
    for series_id, series in (details.get("SERIES") or {}).items():
        s_path = f"$.DETAILS.SERIES.{series_id}"
        require(series, "series", s_path)
        typecheck(series, s_path)
        if series.get("SERIES_ID") != series_id:
            warnings.append(f"{s_path}.SERIES_ID {series.get('SERIES_ID')!r} != map key {series_id!r}")
        if series.get("CURRENCY_CODE") != deal_ccy:
            warnings.append(f"{s_path}.CURRENCY_CODE != DETAILS.CURRENCY_CODE")
        if series.get("POOL_SIZE", 0) <= series.get("SERIES_SIZE", 0):
            warnings.append(f"{s_path}.POOL_SIZE must exceed SERIES_SIZE (§20.6.2)")

        tranche_total = 0
        ce_by_class: Dict[str, set] = {}
        ratings_by_class: Dict[str, set] = {}
        ranking_by_class: Dict[str, set] = {}
        class_order: List[str] = []
        maturities: List[int] = []

        for tranche_id, tranche in (series.get("TRANCHES") or {}).items():
            t_path = f"{s_path}.TRANCHES.{tranche_id}"
            require(tranche, "tranche", t_path)
            typecheck(tranche, t_path)
            if tranche.get("TRANCHE_ID") != tranche_id:
                warnings.append(f"{t_path}.TRANCHE_ID != map key {tranche_id!r}")
            if tranche.get("SERIES_ID") != series_id:
                warnings.append(f"{t_path}.SERIES_ID != enclosing series {series_id!r}")
            if tranche.get("CCY") != deal_ccy:
                warnings.append(f"{t_path}.CCY != DETAILS.CURRENCY_CODE (§20.6.6)")

            size = tranche.get("TRANCHE_SIZE", 0)
            tranche_total += size
            if tranche.get("INITIAL_NOTE_BALANCE") != size:
                warnings.append(f"{t_path}.INITIAL_NOTE_BALANCE != TRANCHE_SIZE (§20.6.1)")

            discount = tranche.get("UNDERWRITING_DISC_AND_COMMISIONS")
            if isinstance(discount, (int, float)):
                expected = round(size * (1 - discount / 100.0), 2)
                got = tranche.get("NET_PROCEEDS")
                if got is None or abs(float(got) - expected) > 0.01:
                    warnings.append(f"{t_path}.NET_PROCEEDS {got} != {expected} (§20.6.5)")

            coupon = tranche.get("COUPON_TYPE")
            spread = tranche.get("SPREAD_BPS")
            curve = tranche.get("CURVE_REFERENCE") or ""
            if coupon == "Float":
                for absent in ("INT_RATE", "YIELD"):
                    if absent in tranche:
                        warnings.append(f"{t_path}.{absent} must be omitted on a float tranche (§20.4.2)")
                if "BENCHMARK" not in tranche:
                    warnings.append(f"{t_path}.BENCHMARK missing on a float tranche (§20.4.2)")
                if isinstance(spread, int) and spread % 10:
                    warnings.append(f"{t_path}.SPREAD_BPS {spread} is not a multiple of 10 (§20.4.3)")
                if curve != f"{tranche.get('BENCHMARK')} + {spread} bps":
                    warnings.append(f"{t_path}.CURVE_REFERENCE {curve!r} disagrees with SPREAD_BPS")
            elif coupon == "Fixed":
                if tranche.get("INT_RATE") != tranche.get("YIELD"):
                    warnings.append(f"{t_path}.YIELD != INT_RATE (§20.6.5)")
                if "BENCHMARK" in tranche:
                    warnings.append(f"{t_path}.BENCHMARK must be omitted on a fixed tranche (§20.4.1)")
                if curve != f"I-Curve + {spread} bps":
                    warnings.append(f"{t_path}.CURVE_REFERENCE {curve!r} disagrees with SPREAD_BPS")

            redemption = tranche.get("ANT_REDEMPTION_DATE")
            maturity = tranche.get("MATURITY_DATE")
            if isinstance(redemption, int) and isinstance(maturity, int) and redemption >= maturity:
                warnings.append(f"{t_path}.ANT_REDEMPTION_DATE must precede MATURITY_DATE (§20.7.1)")
            if isinstance(maturity, int):
                maturities.append(maturity)

            cls = tranche.get("TRANCHE_CLASS", "")
            cc = credit_class(cls)
            if cc not in class_order:
                class_order.append(cc)
            ce_by_class.setdefault(cc, set()).add(tranche.get("CREDIT_ENHANCEMENT_PCT"))
            ratings_by_class.setdefault(cc, set()).add(
                (tranche.get("TRANCHE_FITCH"), tranche.get("TRANCHE_MOODYS"), tranche.get("TRANCHE_SP")))
            ranking_by_class.setdefault(cc, set()).add(tranche.get("RANKING"))

            for security_id, security in (tranche.get("SECURITIES") or {}).items():
                c_path = f"{t_path}.SECURITIES.{security_id}"
                require(security, "security", c_path)
                typecheck(security, c_path)
                if security.get("SECURITY_ID") != security_id:
                    warnings.append(f"{c_path}.SECURITY_ID != map key {security_id!r}")
                if security.get("TRANCHE_ID") != tranche_id:
                    warnings.append(f"{c_path}.TRANCHE_ID != enclosing tranche {tranche_id!r}")
                if security.get("SERIES_ID") != series_id:
                    warnings.append(f"{c_path}.SERIES_ID != enclosing series {series_id!r}")
                if security.get("SECURITY_CLASS") != cls:
                    warnings.append(f"{c_path}.SECURITY_CLASS != parent TRANCHE_CLASS (§20.3.5)")

        if tranche_total != series.get("SERIES_SIZE"):
            warnings.append(f"{s_path}.SERIES_SIZE {series.get('SERIES_SIZE')} "
                            f"!= sum of tranches {tranche_total} (§20.6.1)")
        series_total += series.get("SERIES_SIZE", 0)

        for cc, values in ce_by_class.items():
            if len(values) > 1:
                warnings.append(f"{s_path} credit class {cc}: CREDIT_ENHANCEMENT_PCT differs "
                                f"within the class {sorted(map(str, values))} (§20.6.3)")
        for cc, values in ratings_by_class.items():
            if len(values) > 1:
                warnings.append(f"{s_path} credit class {cc}: ratings differ within the class (§20.6.4)")
        for cc, values in ranking_by_class.items():
            if len(values) > 1:
                warnings.append(f"{s_path} credit class {cc}: RANKING differs within the class (§20.6.4)")
        ce_sequence = [next(iter(ce_by_class[cc])) for cc in class_order]
        for a, b in zip(ce_sequence, ce_sequence[1:]):
            if a is None or b is None or not a > b:
                warnings.append(f"{s_path}: CREDIT_ENHANCEMENT_PCT must fall across classes, "
                                f"got {ce_sequence} (§20.6.3)")
                break
        if maturities:
            expected_final = max(maturities) + D.ONE_DAY_MS
            if series.get("MATURITY_DATE") != expected_final:
                warnings.append(f"{s_path}.MATURITY_DATE != latest tranche maturity + 1 day (§20.7.1)")

    if series_total != details.get("DEAL_SIZE"):
        warnings.append(f"$.DETAILS.DEAL_SIZE {details.get('DEAL_SIZE')} "
                        f"!= sum of series {series_total} (§20.6.1)")

    return {"errors": errors, "warnings": warnings}


# ── HTTP (identical to Loans — §20.2) ──────────────────────────────────────────

def _login(host: str, username: str, password: str, verify_ssl: bool) -> str:
    auth_url = f"https://{host}/sm/event-login-auth"
    body = {"MESSAGE_TYPE": "TXN_LOGIN_AUTH", "SERVICE_NAME": "AUTH_MANAGER",
            "DETAILS": {"USER_NAME": username, "PASSWORD": password}}
    headers = {"Content-Type": "application/json", "SOURCE_REF": "12345",
               "User-Agent": "PostmanRuntime/7.54.0"}
    try:
        resp = requests.post(auth_url, json=body, headers=headers, timeout=30, verify=verify_ssl)
    except requests.RequestException as exc:
        raise RuntimeError(f"Auth connection error: {exc}")
    if resp.status_code >= 400:
        raise RuntimeError(f"Auth failed (HTTP {resp.status_code}): {resp.text[:300]}")
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError("Auth response was not valid JSON")
    token = data.get("SESSION_AUTH_TOKEN")
    if not token:
        raise RuntimeError(f"No SESSION_AUTH_TOKEN in auth response: {data}")
    return token


def _publish(url: str, headers: Dict, payload: Dict, verify_ssl: bool) -> Tuple[str, Any]:
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=60, verify=verify_ssl)
    except requests.RequestException as exc:
        return "HTTP_ERROR", {"error": str(exc)}
    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}
    if isinstance(data, dict) and data.get("MESSAGE_TYPE") == "EVENT_NACK":
        return "NACK", data
    if resp.status_code >= 400:
        return "HTTP_ERROR", {"status_code": resp.status_code, "body": data}
    return "ACK", data


# ── entry point ────────────────────────────────────────────────────────────────

def run_securitized(params: Dict[str, Any], env: Dict[str, Any],
                    stop_event: threading.Event, log_queue: queue.Queue) -> None:
    try:
        _run(params, env, stop_event, log_queue)
    except Exception as exc:
        log_queue.put({"type": "log", "level": "error", "msg": f"Fatal error: {exc}"})
    finally:
        log_queue.put(None)


def _tranche_log_line(tag: str, tranche: Dict[str, Any], n_sec: int) -> str:
    """§20.10.1 — a float tranche prints its benchmark where a fixed one prints its coupon."""
    fields = tranche
    if fields.get("COUPON_TYPE") == "Float":
        rate_col = str(fields.get("BENCHMARK", ""))
    else:
        rate_col = f"{fields.get('INT_RATE', 0):.2f}%"
    ratings = (f"{fields.get('TRANCHE_FITCH', '')}/{fields.get('TRANCHE_MOODYS', '')}/"
               f"{fields.get('TRANCHE_SP', '')}")
    return (f"{tag}     {fields.get('TRANCHE_CLASS', ''):<4} {fields.get('TRANCHE_SIZE', 0):>15,}  "
            f"{fields.get('COUPON_TYPE', ''):<5} {rate_col:>10}  "
            f"{'+' + str(fields.get('SPREAD_BPS', 0)) + 'bp':>7}  "
            f"{ratings:<15} "
            f"CE {fields.get('CREDIT_ENHANCEMENT_PCT', 0):>5.1f}%  {n_sec} sec")


def _run(params: Dict[str, Any], env: Dict[str, Any],
         stop_event: threading.Event, log_queue: queue.Queue) -> None:

    def log(msg: str, level: str = "info") -> None:
        log_queue.put({"type": "log", "level": level, "msg": msg})

    host = env.get("host_name", "")
    verify_ssl = env.get("verify_ssl", True)
    creds = env.get("credentials", {}).get("bonds_loans", {})
    username = creds.get("username", "")
    password = creds.get("password", "")

    ref_dir = str(params.get("ref_dir") or "")
    log(f"Loading reference data from {ref_dir or '(no ref_dir — using fallbacks)'} ...")
    ref = ReferenceData(ref_dir)
    if ref.missing:
        log(f"Missing reference file(s), using fallbacks: {', '.join(ref.missing)}", "warn")
    log(f"Reference data ready — {len(ref.entities)} entity families, "
        f"{len(ref.asset_types)} asset types, {len(ref.currencies)} currencies.")

    deals = int(params.get("deals", 1) or 1)
    series_per_deal = _as_int_list(params.get("series_per_deal"), [1])
    tranches_per_series = _as_int_list(params.get("tranches_per_series"), [3])
    securities_per_tranche = _as_int_list(params.get("securities_per_tranche"), [2])
    delay = float(params.get("delay", 1.0) or 0)
    dry_run = bool(params.get("dry_run", False))
    currency = (params.get("currency") or "").strip().upper()
    deal_type = (params.get("deal_type") or "").strip().upper()
    coupon_type = (params.get("coupon_type") or "Fixed").strip().title()

    if coupon_type not in ("Fixed", "Float", "Mixed"):
        log(f"Unsupported coupon_type '{coupon_type}' — falling back to Fixed "
            f"(the other 14 platform values are out of scope for v1, §20.5.5).", "warn")
        coupon_type = "Fixed"
    if currency and currency not in ref.currencies:
        log(f"Unknown currency '{currency}'. Available: {', '.join(sorted(ref.currencies))}", "error")
        return
    if deal_type and deal_type not in DEAL_TYPES:
        log(f"Unknown deal_type '{deal_type}'. Expected one of {', '.join(DEAL_TYPES)} — "
            f"drawing at random per deal.", "warn")
        deal_type = ""

    # §20.9 — beyond the realistic range is allowed, not clamped, but it is logged.
    if any(n > 4 for n in series_per_deal):
        log(f"series_per_deal {series_per_deal} exceeds the realistic max of 4 — proceeding.", "warn")
    if any(n > 6 for n in tranches_per_series):
        log(f"tranches_per_series {tranches_per_series} exceeds the realistic max of 6 — proceeding.", "warn")
    clamped = [min(2, max(1, n)) for n in securities_per_tranche]
    if clamped != securities_per_tranche:
        log(f"securities_per_tranche {securities_per_tranche} clamped to {clamped} (1 or 2 only).", "warn")
    securities_per_tranche = clamped

    pool = IdentifierPool()
    gen = DealGenerator(ref, pool, coupon_type=coupon_type,
                        forced_currency=currency, forced_deal_type=deal_type)

    # ── build every deal up front, so a generation error surfaces before any POST ──
    built: List[Dict[str, Any]] = []
    series_cursor = 0
    for d_idx in range(deals):
        n_series = series_per_deal[d_idx % len(series_per_deal)]
        counts = [tranches_per_series[(series_cursor + i) % len(tranches_per_series)]
                  for i in range(n_series)]
        series_cursor += n_series
        deal = gen.generate_deal(n_series, counts, securities_per_tranche)
        built.append(deal)

    log(f"Plan: {deals} deals = {deals} POST(s). dry_run={dry_run}")

    publish_url = f"https://{host}/gwf//{MESSAGE_TYPE}"
    counts_by_status = {"ACK": 0, "NACK": 0, "HTTP_ERROR": 0, "DRY_RUN": 0, "INVALID": 0}

    token = None
    if not dry_run:
        log(f"Authenticating as {username} ...")
        try:
            token = _login(host, username, password, verify_ssl)
        except RuntimeError as exc:
            log(str(exc), "error")
            return
        log("Authenticated.")

    if stop_event.is_set():
        log("Stopped.", "warn")
        return

    pub_headers = {
        "Content-Type": "application/json", "Accept": "*/*",
        "SOURCE_REF": "12345", "Cache-Control": "no-cache",
        "User-Agent": "PostmanRuntime/7.48.0",
        "SESSION_AUTH_TOKEN": token or "",
        "Connection": "keep-alive",
    }

    first = True
    for d_idx, deal in enumerate(built, start=1):
        if stop_event.is_set():
            log("Stopped.", "warn")
            break

        tag = f"[D{d_idx}]"
        payload = _serialise(deal)
        details = payload["DETAILS"]

        # ── structure first, so a NACK can be read against the tree (§20.10.1) ──
        log(f"{tag} {details['ISSUER_NAME']} ({details['ISSUER_TICKER']}) — "
            f"{details['CURRENCY_CODE']} · {deal['meta']['deal_type']} / "
            f"{deal['meta']['asset_type']} · {details.get('INDUSTRY', '')}"
            f"{' / ' + details['SUB_INDUSTRY'] if details.get('SUB_INDUSTRY') else ''} · "
            f"{details['DEAL_SIZE']:,}")
        n_tranches = n_securities = 0
        for series in details["SERIES"].values():
            log(f"{tag}   Series {series['SERIES_ID']} \"{series['SERIES_NAME']}\" — "
                f"{series['SERIES_SIZE']:,} / pool {series['POOL_SIZE']:,} · "
                f"{len(series['TRANCHES'])} tranche(s)")
            for tranche in series["TRANCHES"].values():
                n_sec = len(tranche["SECURITIES"])
                n_tranches += 1
                n_securities += n_sec
                log(_tranche_log_line(tag, tranche, n_sec))

        findings = check_payload(payload)
        for warning in findings["warnings"]:
            log(f"{tag} consistency — {warning}", "warn")

        if findings["errors"]:
            for err in findings["errors"]:
                log(f"{tag} mandatory field missing — {err}", "error")
            log(f"{tag} skipped — the payload fails the §20.3.7 pre-flight check.", "error")
            counts_by_status["INVALID"] += 1
            continue

        if not first and not dry_run and delay:
            if not _interruptible_sleep(delay, stop_event):
                log("Stopped during delay.", "warn")
                break
        first = False

        if dry_run:
            counts_by_status["DRY_RUN"] += 1
            log(f"{tag} DRY RUN — {len(details['SERIES'])} series · "
                f"{n_tranches} tranches · {n_securities} securities")
            log(json.dumps(payload, indent=2))
            continue

        if stop_event.is_set():
            log("Stopped.", "warn")
            break

        status, resp = _publish(publish_url, pub_headers, payload, verify_ssl)
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        if status == "ACK":
            log(f"{tag} ACK — {len(details['SERIES'])} series · "
                f"{n_tranches} tranches · {n_securities} securities", "success")
        elif status == "NACK":
            errs = resp.get("ERROR", [])
            reasons = [e.get("TEXT", str(e)) for e in errs] if errs else [str(resp)]
            log(f"{tag} NACK ({len(reasons)} error(s)) — {reasons[0]}", "error")
            for extra in reasons[1:]:
                log(f"{tag}      also — {extra}", "error")
        else:
            log(f"{tag} HTTP_ERROR — {resp}", "error")

    ok = counts_by_status["ACK"] + counts_by_status["DRY_RUN"]
    bad = counts_by_status["NACK"] + counts_by_status["HTTP_ERROR"] + counts_by_status["INVALID"]
    log(f"Done — ACK={counts_by_status['ACK']}  NACK={counts_by_status['NACK']}  "
        f"HTTP_ERROR={counts_by_status['HTTP_ERROR']}  DRY_RUN={counts_by_status['DRY_RUN']}"
        + (f"  INVALID={counts_by_status['INVALID']}" if counts_by_status["INVALID"] else ""),
        "success" if bad == 0 else "warn")
    log_queue.put({"type": "summary", "success": ok, "failed": bad, "total": ok + bad})
