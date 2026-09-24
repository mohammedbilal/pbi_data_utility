"""Munis engine — spec §21.

One deal is one POST of ``EVENT_CREATE_NEW_MUNIS_ISSUANCE``. Like Securitized the event
carries the whole tree — ``DETAILS`` (deal) -> ``SERIES`` -> ``TRANCHES`` -> ``SECURITIES``
— but unlike Securitized every child collection is a plain **array**, not a ``TEMP-*``
keyed map, and no node carries an id of its own (§21.3.1). There is no linkage query and
no per-tranche loop.

Shape of this module, in the order the run uses it:

* ``ReferenceData``  — loads ``backend/reference/munis/*.csv``; every file optional with a
  hardcoded fallback (§21.5.1).
* ``DealGenerator``  — issuer -> deal -> series -> maturity ladder -> security. The whole
  point of the module lives here: the issuer row is drawn **once** and every
  issuer-dependent field is then read off it rather than drawn again (§21.6.1).
* ``check_payload`` — the pre-flight pass: §21.3.7 mandatory fields (errors) and §21.6
  internal consistency (warnings). Runs before every POST, live or dry.
* ``run_munis``     — the engine contract (`CLAUDE.md`): log_queue dicts only, stop_event
  before every HTTP call and inside every delay, summary + ``None`` sentinel at the end.

Two things separate this tool from the other three, both learned from the 2,142-row
``master/MUNIS_DATA.csv`` analysis recorded in §21.6:

1. **``MATURITY_AMOUNT`` is denominated in thousands.** ``SERIES_SIZE`` is
   ``1000 * sum(MATURITY_AMOUNT)`` — 234 of 235 real series agree exactly. Emitting the
   two in the same unit passes ingest and produces a deal 1,000x the size it claims.
2. **``MATURITY_DESCRIPTION`` is generated, not drawn.** It has three real forms and the
   form is chosen by tax status, not at random (§21.6.5) — all 61 observed
   ``bps over yld`` descriptions sit on a ``Taxable`` or ``Various`` series and none on a
   tax-exempt one.

The generator is **not** seeded — it uses the module-level ``random`` like the other
engines, so consecutive runs differ. ``reference/munis/_generate.py`` (which is
deterministic) is a one-off builder and is never called from here.
"""
from __future__ import annotations

import csv
import json
import queue
import random
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from engines.abs_utils.identifiers import IdentifierPool, cusip as make_cusip
from engines.url_utils import join_url

# ── constants ──────────────────────────────────────────────────────────────────

MESSAGE_TYPE = "EVENT_CREATE_NEW_MUNIS_ISSUANCE"

TRADE_DESK = "Munis"
INVESTOR_STATUS = "PENDING"
SERIES_STATUS = "ACTIVE"
MATURITY_STATUS = "ACTIVE"
SECURITY_STATUS = "ACTIVE"
TRANCHE_CURRENCY = "USD"
ISSUER_COUNTRY = "US"
MINIMUM_PIECE = 1000
MINIMUM_INCREMENT = 1000

# §21.3.3 — a muni deal is quoted in $5k increments; the ladder is drawn in thousands
# because MATURITY_AMOUNT is itself in thousands.
MATURITY_QUANTUM = 5  # i.e. $5,000

# §21.6.4 — DEAL_STATUS and WIRE_TYPE are not independent. Observed pairings only.
STATUS_WIRE_TYPES: Dict[str, List[str]] = {
    "Not Published":           ["PREL PRICING", "FINAL PRICING"],
    "Price Ideas":             ["PREL PRICING"],
    "Week Of":                 ["PREL PRICING"],
    "Day-to-Day":              ["PREL PRICING"],
    "Preliminary Pricing":     ["PREL PRICING", "ALLOTS"],
    "Retail Order Period":     ["FINAL PRICING", "PREL PRICING", "ALLOTS"],
    "Awaiting Allotments":     ["FINAL PRICING", "CUSIP", "REPRICE", "PREL PRICING"],
    "Allotments Available":    ["ALLOTS", "FINAL PRICING", "CUSIP", "REPRICE"],
    "Verbal Award/CXL Window": ["ALLOTS", "FINAL PRICING", "PREL PRICING"],
}
DEAL_STATUSES = list(STATUS_WIRE_TYPES)

# §21.6.3 — SOURCE_OF_REPAYMENT is only ever populated on general-obligation style
# deals; every one of the 13 observed non-null rows sits under General Purposes.
REPAYMENT_SECTORS = {"General Purposes"}

# §21.6.6 — credit enhancement is a state-level programme or a state-active insurer, so
# it cannot be drawn independently of STATE. "Pennsylvania State Aid Intercept Program"
# on a California deal is the exact error this table exists to prevent.
ENHANCEMENT_STATES: Dict[str, List[str]] = {
    "Pennsylvania State Aid Intercept Program": ["PA"],
    "Assured Guaranty Inc": ["CA", "NY", "TX", "IL", "FL", "NJ", "PA"],
    "Municipal Bond Insurance": ["TX", "CA", "NY", "FL", "IL", "OH", "MI"],
    "State Aid": ["NY", "PA", "NJ", "MA"],
    "Insurance": ["NY", "CA", "TX", "FL"],
}

TAX_STATUSES = ["Tax-Exempt", "Taxable", "AMT", "Corp", "Various"]
MONEY_TYPES = ["New Money", "Refunding", "Remarketing"]

# §21.6.7 — the treasury benchmarks a taxable series is quoted against. Coupon and
# maturity travel together; a 4.125% coupon on a 2046 treasury is not a real benchmark.
TREASURY_BENCHMARKS: List[Tuple[float, int, int]] = [  # (coupon, maturity year offset, month)
    (3.875, 2, 3), (3.875, 3, 4), (3.625, 4, 10), (3.875, 5, 3),
    (4.125, 5, 10), (3.750, 6, 10), (4.250, 7, 3), (4.500, 7, 11),
    (4.125, 10, 2), (4.375, 10, 5), (4.625, 20, 2), (4.625, 30, 11),
]

MANDATORY_DEAL = ["DEAL_DESCRIPTION", "DEAL_SIZE", "DEAL_STATUS", "DEAL_TYPE", "STATE"]
MANDATORY_SERIES = ["SERIES_CODE", "SERIES_SIZE", "SERIES_STATUS", "TAX_STATUS"]
MANDATORY_TRANCHE = ["MATURITY_DATE", "MATURITY_AMOUNT", "MATURITY_STATUS", "COUPON", "PRICE"]

SERIES_LETTERS = "ABCDEFG"

# ── reference fallbacks ────────────────────────────────────────────────────────

ISSUERS_FALLBACK = [
    {"NAME": "State Of California General Obligation Bonds Various Purpose",
     "STATE": "CA", "SECTOR": "General Purposes", "PURPOSE": "GEN",
     "TAX_STATUS": "Tax-Exempt", "MOODYS": "Aa2", "SP": "AA-", "FITCH": "AA",
     "SOURCE_OF_REPAYMENT": "General Fund", "MONEY_TYPE": "New Money",
     "ENHANCEMENT": "", "TYPICAL_SIZE": "1500000000"},
    {"NAME": "New York City Housing Development Corporation Multi-Family Housing Revenue Bonds",
     "STATE": "NY", "SECTOR": "Housing", "PURPOSE": "MFH",
     "TAX_STATUS": "Tax-Exempt", "MOODYS": "Aa2", "SP": "AA+", "FITCH": "NR",
     "SOURCE_OF_REPAYMENT": "", "MONEY_TYPE": "New Money",
     "ENHANCEMENT": "", "TYPICAL_SIZE": "500000000"},
    {"NAME": "The Regents Of The University Of California General Revenue Bonds",
     "STATE": "CA", "SECTOR": "Education", "PURPOSE": "EDU",
     "TAX_STATUS": "Tax-Exempt", "MOODYS": "Aa2", "SP": "AA", "FITCH": "AA",
     "SOURCE_OF_REPAYMENT": "", "MONEY_TYPE": "New Money",
     "ENHANCEMENT": "", "TYPICAL_SIZE": "1000000000"},
]

PURPOSES_FALLBACK = [
    {"CODE": "GEN", "SECTOR": "General Purposes", "WEIGHT": "263"},
    {"CODE": "EDU", "SECTOR": "Education", "WEIGHT": "44"},
    {"CODE": "SCH", "SECTOR": "Education", "WEIGHT": "161"},
    {"CODE": "HLT", "SECTOR": "Health Care", "WEIGHT": "65"},
    {"CODE": "HSG", "SECTOR": "Housing", "WEIGHT": "341"},
    {"CODE": "WTR", "SECTOR": "Water & Sewer", "WEIGHT": "126"},
    {"CODE": "TRA", "SECTOR": "Transportation", "WEIGHT": "101"},
    {"CODE": "PWR", "SECTOR": "Power", "WEIGHT": "113"},
]

RATINGS_FALLBACK = [
    {"RANK": "1", "MOODYS": "Aaa", "SP": "AAA", "FITCH": "AAA", "WEIGHT": "120"},
    {"RANK": "2", "MOODYS": "Aa1", "SP": "AA+", "FITCH": "AA+", "WEIGHT": "200"},
    {"RANK": "3", "MOODYS": "Aa2", "SP": "AA", "FITCH": "AA", "WEIGHT": "300"},
    {"RANK": "4", "MOODYS": "Aa3", "SP": "AA-", "FITCH": "AA-", "WEIGHT": "180"},
    {"RANK": "5", "MOODYS": "A1", "SP": "A+", "FITCH": "A+", "WEIGHT": "90"},
    {"RANK": "6", "MOODYS": "A2", "SP": "A", "FITCH": "A", "WEIGHT": "60"},
    {"RANK": "7", "MOODYS": "A3", "SP": "A-", "FITCH": "A-", "WEIGHT": "40"},
    {"RANK": "8", "MOODYS": "Baa1", "SP": "BBB+", "FITCH": "BBB+", "WEIGHT": "10"},
    {"RANK": "9", "MOODYS": "Baa2", "SP": "BBB", "FITCH": "BBB", "WEIGHT": "10"},
    {"RANK": "10", "MOODYS": "Baa3", "SP": "BBB-", "FITCH": "BBB-", "WEIGHT": "5"},
]

SYNDICATE_FALLBACK = [
    {"NAME": "BofA Securities", "WEIGHT": "1697"},
    {"NAME": "J.P. Morgan Securities LLC", "WEIGHT": "979"},
    {"NAME": "Morgan Stanley Co. LLC", "WEIGHT": "936"},
    {"NAME": "Wells Fargo Bank, N.A. Municipal Finance Group", "WEIGHT": "901"},
    {"NAME": "Jefferies LLC", "WEIGHT": "877"},
    {"NAME": "RBC Capital Markets", "WEIGHT": "805"},
    {"NAME": "Raymond James & Associates, Inc.", "WEIGHT": "600"},
    {"NAME": "Barclays Capital Inc.", "WEIGHT": "500"},
    {"NAME": "Goldman Sachs & Co. LLC", "WEIGHT": "400"},
    {"NAME": "Siebert Williams Shank & Co., LLC", "WEIGHT": "300"},
]

BND_BANKS_FALLBACK = [
    {"NAME": "BofA Securities", "WEIGHT": "725"},
    {"NAME": "J.P. Morgan Securities LLC", "WEIGHT": "160"},
    {"NAME": "Jefferies LLC", "WEIGHT": "179"},
    {"NAME": "Morgan Stanley Co. LLC", "WEIGHT": "114"},
]

CALL_FEATURES_FALLBACK = [
    {"TEXT": "NMO.", "WEIGHT": "3"},
    {"TEXT": "**MWC**.", "WEIGHT": "2"},
]

VOCAB_FALLBACK = [
    {"FIELD": "DEAL_TYPE", "VALUE": "Negotiated", "WEIGHT": "2135"},
    {"FIELD": "DEAL_TYPE", "VALUE": "Competitive", "WEIGHT": "7"},
    {"FIELD": "SERIES_MONEY_TYPE", "VALUE": "New Money", "WEIGHT": "1773"},
    {"FIELD": "SERIES_MONEY_TYPE", "VALUE": "Refunding", "WEIGHT": "318"},
    {"FIELD": "SERIES_MONEY_TYPE", "VALUE": "Remarketing", "WEIGHT": "41"},
    {"FIELD": "INTEREST_TYPE", "VALUE": "Fixed", "WEIGHT": "2130"},
    {"FIELD": "INTEREST_TYPE", "VALUE": "Variable", "WEIGHT": "2"},
    {"FIELD": "SECURITY_TYPE", "VALUE": "Bond", "WEIGHT": "100"},
    {"FIELD": "MULTIPLES", "VALUE": "5", "WEIGHT": "1900"},
    {"FIELD": "MULTIPLES", "VALUE": "1", "WEIGHT": "200"},
    {"FIELD": "SOURCE_OF_REPAYMENT", "VALUE": "General Fund", "WEIGHT": "6"},
    {"FIELD": "SOURCE_OF_REPAYMENT", "VALUE": "Personal Income Tax Revenue", "WEIGHT": "5"},
    {"FIELD": "SOURCE_OF_REPAYMENT", "VALUE": "Sales Tax Revenue", "WEIGHT": "2"},
]


class ReferenceData:
    """Every CSV under ``reference/munis`` — all optional, all with a fallback."""

    def __init__(self, ref_dir: str) -> None:
        self.dir: Optional[Path] = Path(ref_dir) if ref_dir else None
        self.missing: List[str] = []

        self.issuers = self._rows("issuers.csv", ISSUERS_FALLBACK)
        self.purposes = self._rows("purposes.csv", PURPOSES_FALLBACK)
        self.ratings = sorted(self._rows("ratings.csv", RATINGS_FALLBACK),
                              key=lambda r: int(r["RANK"]))
        self.syndicate = self._rows("syndicate.csv", SYNDICATE_FALLBACK)
        self.bnd_banks = self._rows("bnd_banks.csv", BND_BANKS_FALLBACK)
        self.call_features = self._rows("call_features.csv", CALL_FEATURES_FALLBACK)

        self.vocab: Dict[str, List[Tuple[str, float]]] = {}
        for row in self._rows("vocabularies.csv", VOCAB_FALLBACK):
            try:
                weight = float(row.get("WEIGHT") or 0)
            except ValueError:
                weight = 0.0
            self.vocab.setdefault(row["FIELD"], []).append((row["VALUE"], weight))

        # §21.6.2 — the purpose->sector join, so a Housing series never carries an
        # airport purpose code.
        self.purpose_sector: Dict[str, str] = {r["CODE"]: r["SECTOR"] for r in self.purposes}
        self.purposes_by_sector: Dict[str, List[str]] = {}
        for row in self.purposes:
            self.purposes_by_sector.setdefault(row["SECTOR"], []).append(row["CODE"])

        # Index issuers so the UI filters are a lookup rather than a rejection loop.
        self.issuers_by_state: Dict[str, List[Dict]] = {}
        self.issuers_by_sector: Dict[str, List[Dict]] = {}
        for row in self.issuers:
            self.issuers_by_state.setdefault((row.get("STATE") or "").upper(), []).append(row)
            if row.get("SECTOR"):
                self.issuers_by_sector.setdefault(row["SECTOR"], []).append(row)

        self.rank_by_moodys: Dict[str, int] = {
            r["MOODYS"]: int(r["RANK"]) for r in self.ratings}

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

    # -- lookups ---------------------------------------------------------------

    def pick_vocab(self, field: str, default: str = "") -> str:
        """Weighted draw from a controlled vocabulary (§21.5.5); zero-weight rows never win."""
        entries = [(v, w) for v, w in self.vocab.get(field, []) if w > 0]
        if not entries:
            entries = [(v, 1.0) for v, _ in self.vocab.get(field, [])]
        if not entries:
            return default
        values, weights = zip(*entries)
        return random.choices(values, weights=weights, k=1)[0]

    def vocab_values(self, field: str) -> List[str]:
        return [v for v, _ in self.vocab.get(field, [])]

    @staticmethod
    def _weighted(rows: List[Dict], key: str = "NAME") -> str:
        weights = []
        for r in rows:
            try:
                weights.append(max(float(r.get("WEIGHT") or 1), 0.01))
            except ValueError:
                weights.append(1.0)
        return random.choices([r[key] for r in rows], weights=weights, k=1)[0]

    def bnd_bank(self) -> str:
        return self._weighted(self.bnd_banks)

    def call_feature(self) -> str:
        return self._weighted(self.call_features, key="TEXT")

    def rating_triplet(self, rank: int) -> Tuple[str, str, str]:
        idx = max(0, min(rank - 1, len(self.ratings) - 1))
        row = self.ratings[idx]
        return row["MOODYS"], row["SP"], row["FITCH"]

    def syndicate_list(self, n: int) -> List[str]:
        names = [r["NAME"] for r in self.syndicate]
        n = min(n, len(names))
        return random.sample(names, n) if n > 0 else []


# ── small helpers ──────────────────────────────────────────────────────────────

def _as_int_list(value: Any, default: List[int]) -> List[int]:
    """`3` / `"1, 2, 3"` / `[1,2]` -> `[…]`, per the cycling-list convention (§21.9)."""
    if value is None or value == "":
        return list(default)
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        out = [int(p) for p in value.replace(";", ",").split(",") if p.strip()]
        return out or list(default)
    if isinstance(value, (list, tuple)):
        out = [int(p) for p in value if str(p).strip()]
        return out or list(default)
    return list(default)


def _interruptible_sleep(seconds: float, stop_event: threading.Event) -> bool:
    """Sleep in 100ms slices; False if the run was stopped (`CLAUDE.md` engine contract)."""
    remaining = seconds
    while remaining > 0:
        if stop_event.is_set():
            return False
        slice_s = min(0.1, remaining)
        threading.Event().wait(slice_s)
        remaining -= slice_s
    return not stop_event.is_set()


def _epoch_ms(d: date) -> int:
    """Dates go on the wire as **UTC-midnight epoch milliseconds** (§21.3.4).

    Every date in the captured payload is exactly UTC midnight; building the timestamp
    from a naive local datetime shifts it a day either side of the dateline and the
    platform stores the wrong business date.
    """
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def _num(value: float, places: int = 3) -> float:
    return round(float(value), places)


def _split_quantised(total: int, parts: int, quantum: int) -> List[int]:
    """Split ``total`` into ``parts`` random positive multiples of ``quantum``, exactly.

    Exactly is the point: the roll-ups in §21.6.9 are equalities, so the allocation has
    to close rather than come near. The remainder from rounding is pushed onto the last
    part, which is why nothing here is re-normalised afterwards.
    """
    parts = max(1, parts)
    units = max(parts, total // quantum)
    weights = [random.uniform(0.35, 1.0) for _ in range(parts)]
    scale = sum(weights)
    out = [max(1, int(units * w / scale)) for w in weights]
    drift = units - sum(out)
    # Spread the drift one unit at a time so no single part absorbs a visible lump.
    i = 0
    while drift > 0:
        out[i % parts] += 1
        drift -= 1
        i += 1
    while drift < 0:
        if out[i % parts] > 1:
            out[i % parts] -= 1
            drift += 1
        i += 1
    return [u * quantum for u in out]


# ── generation ─────────────────────────────────────────────────────────────────

class DealGenerator:
    """Issuer -> deal -> series -> maturity ladder -> security.

    The ordering is the design. The issuer row is drawn once per deal and every
    issuer-dependent field (state, sector, purpose, tax status, ratings, repayment
    source, enhancement) is then *read off it* rather than drawn again — that is what
    §21.6.1 means by issuer-dependent, and it is the difference between plausible test
    data and a Pennsylvania programme guaranteeing a California deal.
    """

    def __init__(self, ref: ReferenceData, pool: IdentifierPool,
                 forced_state: str = "", forced_sector: str = "",
                 forced_tax_status: str = "", forced_deal_status: str = "",
                 roadshow: Optional[bool] = None) -> None:
        self.ref = ref
        self.pool = pool
        self.forced_state = (forced_state or "").upper()
        self.forced_sector = forced_sector or ""
        self.forced_tax_status = forced_tax_status or ""
        self.forced_deal_status = forced_deal_status or ""
        # None = draw per deal; True/False = force. See §21.6.13 for why this is a
        # first-class control rather than a 20% draw.
        self.roadshow = roadshow

    # -- issuer ----------------------------------------------------------------

    def candidate_issuers(self) -> List[Dict]:
        rows = self.ref.issuers
        if self.forced_state:
            rows = [r for r in rows if (r.get("STATE") or "").upper() == self.forced_state]
        if self.forced_sector:
            rows = [r for r in rows if (r.get("SECTOR") or "") == self.forced_sector]
        return rows

    def pick_issuer(self) -> Dict:
        rows = self.candidate_issuers()
        return dict(random.choice(rows or self.ref.issuers))

    # -- dates -----------------------------------------------------------------

    def _timeline(self) -> Dict[str, date]:
        """The deal calendar, anchored on TRADE_DATE (§21.6.8).

        Offsets are those of the captured sample, which is the app's own output and so
        the authority on what the platform expects. ``master/MUNIS_DATA.csv`` agrees on
        EXPECTED_PRICING (T-2 exactly, 139/139) but stores AWARD_DATE *before* the trade
        date in all 81 populated rows — a DB-side artefact the payload does not share.
        """
        trade = date.today() + timedelta(days=random.randint(3, 45))
        # Delivery is derived from the dated date, not drawn beside it: settlement
        # follows dating, and 232 of 234 real series have DATED_DATE <= DELIVERY_DATE.
        dated = trade + timedelta(days=random.randint(5, 14))
        return {
            "announcement": trade - timedelta(days=random.randint(12, 24)),
            "expected_pricing": trade - timedelta(days=2),
            "order_begin": trade - timedelta(days=1),
            "trade": trade,
            "order_end": trade + timedelta(days=1),
            "award": trade + timedelta(days=1),
            "dated": dated,
            "delivery": dated + timedelta(days=random.randint(0, 7)),
        }

    @staticmethod
    def _first_coupon(dated: date) -> date:
        """Munis pay on the 1st or the 15th; the first coupon is the next such date
        at least four months out (§21.6.8)."""
        target = _add_months(dated, random.choice([4, 5, 6, 7]))
        day = random.choice([1, 15])
        base = date(target.year, target.month, min(day, 28))
        return base if base > dated else _add_months(base, 1)

    # -- ratings ---------------------------------------------------------------

    def _base_rank(self, issuer: Dict) -> int:
        """The issuer's own rating, as a rank on the aligned ladder."""
        rank = self.ref.rank_by_moodys.get(issuer.get("MOODYS", ""))
        if rank:
            return rank
        # NR / Various / blank issuers still need a credit level to notch from.
        return random.choices(range(1, len(self.ref.ratings) + 1),
                              weights=[int(r.get("WEIGHT") or 1) for r in self.ref.ratings],
                              k=1)[0]

    # -- deal ------------------------------------------------------------------

    def generate_deal(self, n_series: int, maturity_counts: Sequence[int],
                      securities_per_maturity: Sequence[int]) -> Dict[str, Any]:
        issuer = self.pick_issuer()
        state = (issuer.get("STATE") or "CA").upper()
        tl = self._timeline()
        base_rank = self._base_rank(issuer)

        # §21.6.10 — whether the series ratings may diverge is a **deal-level** decision.
        # Notching each series independently makes the deal rating "Various" about 44% of
        # the time on a 3-series deal; only 10 of 95 real deals are Various.
        notching = random.random() < 0.15

        is_roadshow = self.roadshow if self.roadshow is not None else random.random() < 0.2

        deal_status = self.forced_deal_status or random.choice(DEAL_STATUSES)
        wire_type = random.choice(STATUS_WIRE_TYPES.get(deal_status, ["FINAL PRICING"]))

        # One CUSIP base per deal; the suffix advances once per maturity across the
        # whole deal, which is how a real issuer's maturities are numbered (§21.8.1).
        cusip_base = self.pool.new_cusip_base()

        syndicate = self.ref.syndicate_list(random.randint(4, 14))
        bnd_bank = self.ref.bnd_bank()

        # §21.6.9 — deal size is issuer-dependent: it is scaled from the size that issuer
        # actually brings to market, then allocated *down* the tree so both roll-ups
        # close by construction rather than by adjustment.
        try:
            typical = int(float(issuer.get("TYPICAL_SIZE") or 0))
        except ValueError:
            typical = 0
        target_thousands = max(20_000, int(typical * random.uniform(0.55, 1.35)) // 1000)
        series_targets = _split_quantised(target_thousands, n_series, MATURITY_QUANTUM)

        series_list: List[Dict[str, Any]] = []
        cusip_index = 0
        for s_idx in range(n_series):
            n_mat = maturity_counts[s_idx] if s_idx < len(maturity_counts) else 5
            n_sec = securities_per_maturity[s_idx % len(securities_per_maturity)]
            series, cusip_index = self._generate_series(
                issuer, tl, base_rank, s_idx, n_mat, n_sec, cusip_base, cusip_index,
                series_targets[s_idx], notching)
            series_list.append(series)

        # §21.6.9 — the two roll-ups. DEAL_SIZE is the sum of its series, never drawn.
        deal_size = sum(s["SERIES_SIZE"] for s in series_list)

        details: Dict[str, Any] = {
            "ANNOUNCEMENT_DT": _epoch_ms(tl["announcement"]),
            "BND_BANK": bnd_bank,
            "CHANGE_FLAGS": {"CHILD_UPDATED": True, "EDITED_FIELDS": []},
            "DEAL_DESCRIPTION": issuer["NAME"],
            "DEAL_SIZE": deal_size,
            "DEAL_STATUS": deal_status,
            "DEAL_TYPE": self.ref.pick_vocab("DEAL_TYPE", "Negotiated"),
            "EXPECTED_PRICING_DATE": _epoch_ms(tl["expected_pricing"]),
            "ISSUER_COUNTRY": ISSUER_COUNTRY,
            "IS_ROADSHOW": is_roadshow,
            "ORDER_PERIOD_BEGIN_DATE": _epoch_ms(tl["order_begin"]),
            "ORDER_PERIOD_END_DATE": _epoch_ms(tl["order_end"]),
            "RISK_COUNTRY": ISSUER_COUNTRY,
            "SERIES": series_list,
            "STATE": state,
            "SYNDICATE": "; ".join(syndicate),
            "USE_OF_PROCEEDS": issuer.get("SECTOR") or "None",
            "WIRE_TYPE": wire_type,
        }

        # §21.6.13 — MANDATE_TEXT is conditionally mandatory: a roadshow deal without it
        # NACKs "Mandate text is required for Roadshow." (a StandardError business rule,
        # no PATH). Same rule as ABS §20.3.8. Verified live 2026-09-15 by the NACK.
        # It is omitted rather than sent empty when IS_ROADSHOW is false.
        if is_roadshow:
            details["MANDATE_TEXT"] = (
                f"{bnd_bank} mandated as senior manager on the {issuer['NAME']} "
                f"offering; investor roadshow to follow, pricing expected thereafter."
            )

        return {"DETAILS": details,
                "meta": {"issuer": issuer["NAME"], "state": state,
                         "sector": issuer.get("SECTOR") or "(none)",
                         "base_rank": base_rank}}

    # -- series ----------------------------------------------------------------

    def _generate_series(self, issuer: Dict, tl: Dict[str, date], base_rank: int,
                         s_idx: int, n_mat: int, n_sec: int,
                         cusip_base: str, cusip_index: int,
                         size_thousands: int, notching: bool
                         ) -> Tuple[Dict[str, Any], int]:
        year = tl["trade"].year
        letter = SERIES_LETTERS[s_idx % len(SERIES_LETTERS)]
        series_code = f"{year}{letter}"

        # Series notch off the issuer, never independently: at most one notch, so a
        # deal's series stay within a credit band (§21.6.10).
        notch = random.choice([0, 1]) if notching else 0
        rank = min(len(self.ref.ratings), max(1, base_rank + notch))
        moodys, sp, fitch = self.ref.rating_triplet(rank)

        tax_status = self.forced_tax_status or issuer.get("TAX_STATUS") or "Tax-Exempt"
        sector = issuer.get("SECTOR") or ""
        purpose = issuer.get("PURPOSE") or ""
        # §21.6.2 — if the issuer carries no purpose code, draw one *from its sector*.
        if not purpose:
            purpose = random.choice(self.ref.purposes_by_sector.get(sector) or ["GEN"])
        # "Various" is a deal-level roll-up across differing series, exactly like the
        # rating rule in §21.6.10 — a single series always has one real sector. Resolve
        # it from the purpose code rather than carrying the aggregate down the tree.
        if not sector or sector == "Various":
            sector = self.ref.purpose_sector.get(purpose, sector)

        # §21.6.3 — only GO-style deals name a repayment source.
        repayment = issuer.get("SOURCE_OF_REPAYMENT") or ""
        if not repayment and sector in REPAYMENT_SECTORS and random.random() < 0.35:
            repayment = self.ref.pick_vocab("SOURCE_OF_REPAYMENT", "General Fund")
        if sector not in REPAYMENT_SECTORS:
            repayment = ""

        # §21.6.6 — enhancement must be legal in this state.
        enhancement = issuer.get("ENHANCEMENT") or ""
        state = (issuer.get("STATE") or "").upper()
        if enhancement and state not in ENHANCEMENT_STATES.get(enhancement, [state]):
            enhancement = ""

        description = self._series_description(issuer, series_code, tax_status, sector)
        dated = tl["dated"]
        first_coupon = self._first_coupon(dated)

        maturities, cusip_index = self._generate_maturities(
            n_mat, n_sec, tl, tax_status, rank, description, cusip_base, cusip_index,
            size_thousands)

        # §21.6.9 — MATURITY_AMOUNT is in **thousands**; the series size is not.
        series_size = 1000 * sum(m["MATURITY_AMOUNT"] for m in maturities)

        series: Dict[str, Any] = {
            "AWARD_DATE": _epoch_ms(tl["award"]),
            "CALL_FEATURE_DESCRIPTION": self._call_feature(tl, maturities),
            "CHANGE_FLAGS": {"EDITED_FIELDS": [], "CHILD_UPDATED": True},
            "DATED_DATE": _epoch_ms(dated),
            "DELIVERY_DATE": _epoch_ms(tl["delivery"]),
            "ENHANCEMENT": enhancement or "None",
            "FIRST_COUPON_DATE": _epoch_ms(first_coupon),
            "INTEREST_TYPE": self.ref.pick_vocab("INTEREST_TYPE", "Fixed"),
            "INVESTOR_STATUS": INVESTOR_STATUS,
            "PURPOSE": purpose,
            "SECTOR": sector,
            "SERIES_CODE": series_code,
            "SERIES_DESCRIPTION": description,
            "SERIES_FITCH_RATING": fitch,
            "SERIES_MONEY_TYPE": issuer.get("MONEY_TYPE")
                                 or self.ref.pick_vocab("SERIES_MONEY_TYPE", "New Money"),
            "SERIES_MOODYS_RATING": moodys,
            "SERIES_SANDP_RATING": sp,
            "SERIES_SIZE": series_size,
            "SERIES_STATUS": SERIES_STATUS,
            "TAX_STATUS": tax_status,
            "TRADE_DATE": _epoch_ms(tl["trade"]),
            "TRADE_DESK": TRADE_DESK,
            "TRANCHES": maturities,
        }
        # §21.3.5 — the only field this engine omits where the captured sample carries
        # one. The capture has SOURCE_OF_REPAYMENT: "General Fund" on an *Education*
        # series, but that capture was hand-filled in the UI (its SERIES_DESCRIPTION
        # says Multi-Family Housing while its SECTOR says Education), so it is not
        # evidence of a rule. 2,129 of 2,142 real rows leave the field null, and all 13
        # that populate it are General Purposes. If the schema turns out to require the
        # key unconditionally the NACK will name it — emit it always at that point.
        if repayment:
            series["SOURCE_OF_REPAYMENT"] = repayment
        return series, cusip_index

    @staticmethod
    def _series_description(issuer: Dict, code: str, tax_status: str, sector: str) -> str:
        """The series' own name. MATURITY_DESCRIPTION is built from this, so it has to
        read like a real series title (§21.6.5)."""
        stem = issuer["NAME"]
        # Issuer names in the reference already carry a series suffix often enough that
        # repeating it reads wrong; keep the first clause and re-title it.
        for marker in (" Series ", " Bonds ", " Revenue "):
            if marker in stem:
                stem = stem.split(marker)[0] + marker.rstrip()
                break
        stem = stem.strip()
        qualifier = {"AMT": " (AMT)", "Taxable": " (Taxable)",
                     "Tax-Exempt": "", "Corp": " (Corp CUSIP)"}.get(tax_status, "")
        return f"{stem} {code}{qualifier}".strip()

    def _call_feature(self, tl: Dict[str, date], maturities: List[Dict]) -> str:
        """A real call description, dated off this deal (§21.6.5).

        Most munis are callable ~10 years out at par; the short ladders that finish
        inside the call date are non-callable and say so.
        """
        last = max((m["MATURITY_DATE"] for m in maturities), default=0)
        call_date = _add_months(tl["dated"], 12 * 10)
        if last <= _epoch_ms(call_date):
            return "NMO."
        if random.random() < 0.25:
            return self.ref.call_feature()
        return (f"CALLABLE {call_date:%m/%d/%Y} @100.0, Subject to Special and Optional "
                f"Redemption as described in the POS.")

    # -- maturity ladder -------------------------------------------------------

    def _generate_maturities(self, n_mat: int, n_sec: int, tl: Dict[str, date],
                             tax_status: str, rank: int, series_description: str,
                             cusip_base: str, cusip_index: int, size_thousands: int
                             ) -> Tuple[List[Dict[str, Any]], int]:
        """A serial bond ladder: one maturity a year, rising coupon and yield (§21.6.11).

        Munis are issued as a ladder, not a stack — maturity, yield and price move
        together along it, which is why none of the three is drawn independently.
        """
        first_year_out = random.randint(1, 3)
        day = random.choice([1, 1, 1, 15, 30])
        month = random.randint(1, 12)
        # A par-priced 5% coupon is the muni convention; ~2/3 of the real ladder is 5.00.
        premium_coupon = random.choice([5.0, 5.0, 5.0, 5.25, 4.0])
        base_yield = 2.4 + 0.12 * (rank - 1) + random.uniform(-0.15, 0.15)

        benchmark = random.choice(TREASURY_BENCHMARKS)
        # The ladder's amounts are an exact split of the series target (in thousands),
        # so SERIES_SIZE = 1000 * sum(MATURITY_AMOUNT) holds without adjustment.
        amounts = _split_quantised(size_thousands, n_mat, MATURITY_QUANTUM)
        out: List[Dict[str, Any]] = []
        for i in range(n_mat):
            years = first_year_out + i
            mat = date(tl["trade"].year + years, month, min(day, 28))

            # Yield rises along the curve and flattens at the long end.
            yld = base_yield + 1.35 * (1 - pow(2.718281828, -years / 9.0)) \
                + random.uniform(-0.04, 0.04)
            yld = _num(max(0.5, yld), 3)

            # §21.6.12 — price follows from coupon vs yield, it is never drawn:
            # premium when the coupon beats the yield, par when they match.
            if yld < premium_coupon:
                coupon = premium_coupon
                price = _num(self._price_from(coupon, yld, years), 3)
            else:
                coupon = _num(yld, 3)   # par bond at the long end
                price = 100.0
            spread = int(round((yld - self._mmd(years)) * 100))

            amount = amounts[i]
            securities = []
            for _ in range(max(1, n_sec)):
                securities.append({
                    "SECURITY_STATUS": SECURITY_STATUS,
                    "CUSIP": make_cusip(cusip_base, cusip_index),
                    "CHANGE_FLAGS": {},
                })
                cusip_index += 1

            out.append({
                "CHANGE_FLAGS": {"EDITED_FIELDS": [], "CHILD_UPDATED": True},
                "COUPON": coupon,
                "COUPON_TYPE": "Fixed",
                "MATURITY_AMOUNT": amount,
                "MATURITY_DATE": _epoch_ms(mat),
                "MATURITY_DESCRIPTION": self._maturity_description(
                    tax_status, series_description, mat, spread, benchmark, tl),
                "MATURITY_STATUS": MATURITY_STATUS,
                "MINIMUM_INCREMENT": MINIMUM_INCREMENT,
                "MINIMUM_PIECE": MINIMUM_PIECE,
                "MULTIPLES": int(self.ref.pick_vocab("MULTIPLES", "5") or 5),
                "PRICE": price,
                "SECURITIES": securities,
                "SPREAD": spread,
                "TRANCHE_CURRENCY": TRANCHE_CURRENCY,
                "YIELD": yld,
            })
        return out, cusip_index

    @staticmethod
    def _mmd(years: int) -> float:
        """The AAA municipal benchmark the SPREAD is quoted against."""
        return 2.30 + 1.10 * (1 - pow(2.718281828, -years / 8.0))

    @staticmethod
    def _price_from(coupon: float, yld: float, years: int) -> float:
        """Semi-annual present value of a par bond — the premium a 5% coupon commands
        when the market yields less (§21.6.12)."""
        n = max(1, years * 2)
        c = coupon / 2.0
        y = yld / 2.0 / 100.0
        if y <= 0:
            return 100.0 + c * n
        annuity = (1 - pow(1 + y, -n)) / y
        return c * annuity + 100.0 * pow(1 + y, -n)

    def _maturity_description(self, tax_status: str, series_description: str,
                              mat: date, spread: int,
                              benchmark: Tuple[float, int, int],
                              tl: Dict[str, date]) -> str:
        """Three real forms, chosen by tax status — not drawn at random (§21.6.5).

        All 61 ``bps over yld`` descriptions in ``MUNIS_DATA.csv`` sit on a ``Taxable``
        or ``Various`` series and none on a tax-exempt one, because only a taxable muni
        is quoted against a treasury. The double space after ``yld`` is in the source
        data on every one of the 61 rows; it is reproduced deliberately.
        """
        if tax_status in ("Taxable", "Various") and random.random() < 0.7:
            coupon, yrs_out, month = benchmark
            tsy = date(tl["trade"].year + yrs_out, month, 15)
            stamp = f"{tsy:%m/%d/%Y}" if random.random() < 0.4 else f"{tsy:%m/%Y}"
            return (f"{abs(spread):.2f} bps over yld  of {coupon:.3f} cpn {stamp} tsy")
        if random.random() < 0.55:
            return f"{series_description} Maturity {mat.year}"
        return random.choice([".", "NMO.", "**MWC**."])


# ── pre-flight ─────────────────────────────────────────────────────────────────

def check_payload(payload: Dict[str, Any]) -> Dict[str, List[str]]:
    """§21.3.7 mandatory fields (errors) and §21.6 internal consistency (warnings).

    An ACK does not mean the data is right — the handler validates names, types and
    presence, not whether the sizes add up. These assertions are the only thing that
    catches a broken roll-up before it reaches a downstream report.
    """
    errors: List[str] = []
    warnings: List[str] = []
    details = payload.get("DETAILS") or {}

    for field in MANDATORY_DEAL:
        if details.get(field) in (None, ""):
            errors.append(f"DETAILS.{field}")

    # §21.6.13 — conditionally mandatory: a roadshow deal must carry MANDATE_TEXT, or the
    # platform NACKs "Mandate text is required for Roadshow." Checked here so the run
    # fails on the pre-flight rather than burning a POST.
    if details.get("IS_ROADSHOW") is True and not str(details.get("MANDATE_TEXT") or "").strip():
        errors.append("DETAILS.MANDATE_TEXT is required when IS_ROADSHOW is true (§21.6.13)")

    series_list = details.get("SERIES") or []
    if not series_list:
        errors.append("DETAILS.SERIES is empty")

    total_series = 0
    for s_i, series in enumerate(series_list, start=1):
        for field in MANDATORY_SERIES:
            if series.get(field) in (None, ""):
                errors.append(f"SERIES[{s_i}].{field}")

        maturities = series.get("TRANCHES") or []
        if not maturities:
            errors.append(f"SERIES[{s_i}].TRANCHES is empty")

        total_maturity = 0
        last_date = 0
        for m_i, m in enumerate(maturities, start=1):
            for field in MANDATORY_TRANCHE:
                if m.get(field) in (None, ""):
                    errors.append(f"SERIES[{s_i}].TRANCHES[{m_i}].{field}")
            if not (m.get("SECURITIES") or []):
                errors.append(f"SERIES[{s_i}].TRANCHES[{m_i}].SECURITIES is empty")
            total_maturity += m.get("MATURITY_AMOUNT") or 0

            # §21.6.11 — the ladder must ascend.
            if m.get("MATURITY_DATE", 0) <= last_date:
                warnings.append(f"SERIES[{s_i}] maturity {m_i} does not extend the ladder")
            last_date = m.get("MATURITY_DATE", 0)

            # §21.6.12 — price and yield must agree about the coupon.
            coupon, price, yld = m.get("COUPON"), m.get("PRICE"), m.get("YIELD")
            if None not in (coupon, price, yld):
                if price > 100 and yld >= coupon:
                    warnings.append(
                        f"SERIES[{s_i}] maturity {m_i} priced at a premium ({price}) "
                        f"but yields {yld} on a {coupon} coupon")
                if price == 100 and abs(yld - coupon) > 0.001:
                    warnings.append(
                        f"SERIES[{s_i}] maturity {m_i} priced at par but yield {yld} "
                        f"!= coupon {coupon}")

            # §21.6.8 — a maturity cannot precede delivery.
            if m.get("MATURITY_DATE", 0) <= series.get("DELIVERY_DATE", 0):
                warnings.append(f"SERIES[{s_i}] maturity {m_i} matures before delivery")

        # §21.6.9 — MATURITY_AMOUNT is in thousands.
        expected = 1000 * total_maturity
        if series.get("SERIES_SIZE") != expected:
            warnings.append(
                f"SERIES[{s_i}].SERIES_SIZE {series.get('SERIES_SIZE'):,} != "
                f"1000 x sum(MATURITY_AMOUNT) {expected:,}")
        total_series += series.get("SERIES_SIZE") or 0

        # §21.6.2 — purpose and sector belong to the same family.
        if series.get("SOURCE_OF_REPAYMENT") and series.get("SECTOR") not in REPAYMENT_SECTORS:
            warnings.append(
                f"SERIES[{s_i}] names a repayment source on a "
                f"{series.get('SECTOR')!r} series")

        # §21.6.8 — the series calendar must run forward.
        order = ["TRADE_DATE", "DATED_DATE", "DELIVERY_DATE", "FIRST_COUPON_DATE"]
        seq = [(k, series.get(k)) for k in order if series.get(k)]
        for (ka, va), (kb, vb) in zip(seq, seq[1:]):
            if va > vb:
                warnings.append(f"SERIES[{s_i}] {ka} is after {kb}")

    if details.get("DEAL_SIZE") != total_series:
        warnings.append(f"DETAILS.DEAL_SIZE {details.get('DEAL_SIZE'):,} != "
                        f"sum(SERIES_SIZE) {total_series:,}")

    # §21.6.4 — status and wire type are a pair.
    status, wire = details.get("DEAL_STATUS"), details.get("WIRE_TYPE")
    allowed = STATUS_WIRE_TYPES.get(status or "")
    if allowed and wire not in allowed:
        warnings.append(f"WIRE_TYPE {wire!r} is not observed with DEAL_STATUS {status!r}")

    return {"errors": errors, "warnings": warnings}


def deal_ratings(details: Dict[str, Any]) -> Tuple[str, str, str]:
    """§21.6.10 — the deal's rating is its series' rating, or ``Various`` if they differ.

    Reproduced from the source exactly: every one of the 18 deals whose series disagree
    carries ``Various`` at deal level, and no deal whose series agree does.
    """
    out = []
    for key in ("SERIES_MOODYS_RATING", "SERIES_SANDP_RATING", "SERIES_FITCH_RATING"):
        values = {s.get(key) for s in (details.get("SERIES") or []) if s.get(key)}
        values.discard("NR")
        out.append(values.pop() if len(values) == 1 else ("Various" if values else "NR"))
    return out[0], out[1], out[2]


# ── transport ──────────────────────────────────────────────────────────────────

def _login(host: str, username: str, password: str, verify_ssl: bool) -> str:
    auth_url = join_url(host, "/sm/event-login-auth")
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

def run_munis(params: Dict[str, Any], env: Dict[str, Any],
              stop_event: threading.Event, log_queue: queue.Queue) -> None:
    try:
        _run(params, env, stop_event, log_queue)
    except Exception as exc:
        log_queue.put({"type": "log", "level": "error", "msg": f"Fatal error: {exc}"})
    finally:
        log_queue.put(None)


def _maturity_log_line(tag: str, m: Dict[str, Any]) -> str:
    mat = datetime.fromtimestamp(m["MATURITY_DATE"] / 1000, timezone.utc).date()
    return (f"{tag}     {mat:%Y-%m-%d}  {m['MATURITY_AMOUNT'] * 1000:>14,}  "
            f"cpn {m['COUPON']:>6.3f}  yld {m['YIELD']:>6.3f}  "
            f"px {m['PRICE']:>8.3f}  {'+' + str(m['SPREAD']):>6}bp  "
            f"{len(m['SECURITIES'])} sec")


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
    log(f"Reference data ready — {len(ref.issuers)} issuers, {len(ref.purposes)} purpose "
        f"codes, {len(ref.syndicate)} syndicate members.")

    deals = int(params.get("deals", 1) or 1)
    series_per_deal = _as_int_list(params.get("series_per_deal"), [2])
    maturities_per_series = _as_int_list(params.get("maturities_per_series"), [8])
    securities_per_maturity = _as_int_list(params.get("securities_per_maturity"), [1])
    delay = float(params.get("delay", 1.0) or 0)
    dry_run = bool(params.get("dry_run", False))
    state = (params.get("state") or "").strip().upper()
    sector = (params.get("sector") or "").strip()
    tax_status = (params.get("tax_status") or "").strip()
    deal_status = (params.get("deal_status") or "").strip()

    # "" / "random" -> draw per deal; "yes"/"no" (or a real bool) -> force. §21.6.13.
    raw_roadshow = params.get("roadshow", "")
    if isinstance(raw_roadshow, bool):
        roadshow: Optional[bool] = raw_roadshow
    else:
        roadshow = {"yes": True, "true": True, "no": False, "false": False}.get(
            str(raw_roadshow).strip().lower())

    if tax_status and tax_status not in TAX_STATUSES:
        log(f"Unknown tax_status {tax_status!r}. Expected one of "
            f"{', '.join(TAX_STATUSES)} — using the issuer's own.", "warn")
        tax_status = ""
    if deal_status and deal_status not in STATUS_WIRE_TYPES:
        log(f"Unknown deal_status {deal_status!r}. Expected one of "
            f"{', '.join(DEAL_STATUSES)} — drawing per deal.", "warn")
        deal_status = ""

    pool = IdentifierPool()
    gen = DealGenerator(ref, pool, forced_state=state, forced_sector=sector,
                        forced_tax_status=tax_status, forced_deal_status=deal_status,
                        roadshow=roadshow)
    if roadshow is not None:
        log(f"Roadshow forced {'on' if roadshow else 'off'} for every deal"
            + (" — each deal will carry MANDATE_TEXT." if roadshow else "."))

    # The state/sector filters select an issuer; if they select none, say so rather
    # than silently generating an unfiltered deal.
    candidates = gen.candidate_issuers()
    if not candidates:
        log(f"No issuer matches state={state or 'any'} sector={sector or 'any'}. "
            f"Known states: {', '.join(sorted(ref.issuers_by_state))}", "error")
        return
    if state or sector:
        log(f"{len(candidates)} issuer(s) match the filter.")

    # §21.9 — beyond the realistic range is allowed, not clamped, but it is logged.
    if any(n > 7 for n in series_per_deal):
        log(f"series_per_deal {series_per_deal} exceeds the observed max of 7 — proceeding.",
            "warn")
    if any(n > 31 for n in maturities_per_series):
        log(f"maturities_per_series {maturities_per_series} exceeds the observed max of "
            f"31 — proceeding.", "warn")

    built: List[Dict[str, Any]] = []
    series_cursor = 0
    for d_idx in range(deals):
        n_series = series_per_deal[d_idx % len(series_per_deal)]
        counts = [maturities_per_series[(series_cursor + i) % len(maturities_per_series)]
                  for i in range(n_series)]
        series_cursor += n_series
        built.append(gen.generate_deal(n_series, counts, securities_per_maturity))

    log(f"Plan: {deals} deals = {deals} POST(s). dry_run={dry_run}")

    # Built only when it will be used: a dry run must work with no host configured.
    publish_url = join_url(host, f"/gwf//{MESSAGE_TYPE}") if not dry_run else ""
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

    first = True
    for d_idx, deal in enumerate(built, start=1):
        if stop_event.is_set():
            log("Stopped.", "warn")
            break

        tag = f"[D{d_idx}]"
        details = deal["DETAILS"]
        # §21.3.1 — the Munis envelope carries the token and a per-deal UUID SOURCE_REF
        # in the **body**, not only the headers, exactly as the captured payload does.
        source_ref = str(uuid.uuid4())
        payload = {
            "DETAILS": details,
            "MESSAGE_TYPE": MESSAGE_TYPE,
            "SESSION_AUTH_TOKEN": token or "",
            "SOURCE_REF": source_ref,
        }

        moodys, sp, fitch = deal_ratings(details)
        log(f"{tag} {details['DEAL_DESCRIPTION'][:70]} — {details['STATE']} · "
            f"{deal['meta']['sector']} · {moodys}/{sp}/{fitch} · "
            f"{details['DEAL_SIZE']:,} · {details['DEAL_STATUS']} / {details['WIRE_TYPE']}"
            + (" · roadshow" if details["IS_ROADSHOW"] else ""))

        n_mat = n_sec = 0
        for series in details["SERIES"]:
            log(f"{tag}   Series {series['SERIES_CODE']} \"{series['SERIES_DESCRIPTION'][:52]}\" "
                f"— {series['SERIES_SIZE']:,} · {series['TAX_STATUS']} · "
                f"{series['PURPOSE']}/{series['SECTOR'] or '(no sector)'} · "
                f"{len(series['TRANCHES'])} maturities"
                + (f" · {series['SOURCE_OF_REPAYMENT']}"
                   if series.get("SOURCE_OF_REPAYMENT") else "")
                + (f" · {series['ENHANCEMENT']}"
                   if series["ENHANCEMENT"] != "None" else ""))
            for m in series["TRANCHES"]:
                n_mat += 1
                n_sec += len(m["SECURITIES"])
                log(_maturity_log_line(tag, m))

        findings = check_payload(payload)
        for warning in findings["warnings"]:
            log(f"{tag} consistency — {warning}", "warn")
        if findings["errors"]:
            for err in findings["errors"]:
                log(f"{tag} mandatory field missing — {err}", "error")
            log(f"{tag} skipped — the payload fails the §21.3.7 pre-flight check.", "error")
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
                f"{n_mat} maturities · {n_sec} securities")
            log(json.dumps(payload, indent=2))
            continue

        if stop_event.is_set():
            log("Stopped.", "warn")
            break

        pub_headers = {
            "Content-Type": "application/json", "Accept": "*/*",
            "SOURCE_REF": source_ref, "Cache-Control": "no-cache",
            "User-Agent": "PostmanRuntime/7.48.0",
            "SESSION_AUTH_TOKEN": token or "",
            "Connection": "keep-alive",
        }
        status, resp = _publish(publish_url, pub_headers, payload, verify_ssl)
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        if status == "ACK":
            log(f"{tag} ACK — {len(details['SERIES'])} series · {n_mat} maturities · "
                f"{n_sec} securities", "success")
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
        "success" if not bad else "warn")
    log_queue.put({"type": "summary", "success": ok, "failed": bad, "total": ok + bad})
