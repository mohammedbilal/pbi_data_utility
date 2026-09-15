"""Date sequencing for the Securitized (ABS) tool — spec §20.7.

All dates are epoch milliseconds at UTC midnight; ``to_epoch_ms`` and
``_roll_to_weekday`` are reused from ``engines.loan_utils.dates`` unchanged.

Ordering (§20.7.1, verified against ``backend/tests/fixtures/oakhurst_deal.json``)::

    ANNOUNCEMENT_DT -> EXPECTED_PRICING_DATE -> SETTLEMENT_DATE -> FIRST_COUPON_DT
      2026-08-05          2026-08-12 (+7d)      2026-08-17 (+5d)   2026-09-17 (+31d)

    TRANCHE_SETTLEMENT_DATE >= series SETTLEMENT_DATE
    ANT_REDEMPTION_DATE     ~ tranche settlement + WAL years
    MATURITY_DATE           = ANNOUNCEMENT_DT + TENOR years, rolled to the payment day
    ANT_REDEMPTION_DATE     <  MATURITY_DATE                        (always)
    SERIES.MATURITY_DATE    = max(tranche MATURITY_DATE) + 1 day    (legal final)

Two rules are *derived* from the sample rather than stated in §20.7, and both are
needed to reproduce its golden day-counts:

* **Payment day 15.** Tenors of 3Y/5Y/6Y off a 2026-08-05 announcement land at
  +1106/1836/2202 days, i.e. 2029-08-15 / 2031-08-15 / 2032-08-15 — the anniversary
  rolled *forward* to the 15th, the monthly ABS payment date. A bare anniversary
  would give 1096/1826/2192.
* **Tranche settlement steps by one month** and is then rolled to a weekday. The
  sample's three tranches settle 2026-08-17, 2026-09-17, 2026-10-19 — the third is
  the Saturday 2026-10-17 rolled to Monday.
"""

from __future__ import annotations

import calendar
import random
from datetime import date, timedelta

from engines.loan_utils.dates import _roll_to_weekday, to_epoch_ms

__all__ = [
    "ONE_DAY_MS",
    "PAYMENT_DAY",
    "TENOR_POOL",
    "add_months",
    "add_years",
    "ant_redemption_date",
    "draw_tenor_ladder",
    "next_series_announcement",
    "roll_to_payment_day",
    "series_dates",
    "series_legal_final",
    "to_epoch_ms",
    "tranche_maturity",
    "tranche_settlement",
]

ONE_DAY_MS = 86_400_000

#: The monthly ABS payment date the sample's maturities land on.
PAYMENT_DAY = 15

#: §20.7.2 — tranche tenors are drawn from this pool, ascending, without replacement.
TENOR_POOL = (1, 2, 3, 4, 5, 6, 7, 10)

_PRICING_GAP_DAYS = 7
_SETTLEMENT_GAP_DAYS = 5
_FIRST_COUPON_GAP_DAYS = 31


def add_months(d: date, months: int) -> date:
    """Calendar-month arithmetic, clamping to the last day of the target month."""
    total = (d.year * 12 + d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def add_years(d: date, years: float) -> date:
    """Whole years by anniversary; fractional years as 30-day months, then days."""
    whole = int(years)
    out = add_months(d, whole * 12)
    remainder = years - whole
    if remainder:
        out = add_months(out, int(remainder * 12))
        out += timedelta(days=int(round((remainder * 12 - int(remainder * 12)) * 30)))
    return out


def roll_to_payment_day(d: date, payment_day: int = PAYMENT_DAY) -> date:
    """The next ``payment_day`` on or after ``d`` (never backwards)."""
    day = min(payment_day, calendar.monthrange(d.year, d.month)[1])
    if d.day <= day:
        return date(d.year, d.month, day)
    nxt = add_months(date(d.year, d.month, 1), 1)
    return date(nxt.year, nxt.month, min(payment_day, calendar.monthrange(nxt.year, nxt.month)[1]))


def series_dates(announcement: date) -> dict:
    """The four series-level dates of §20.7.1, as ``date`` objects."""
    pricing = announcement + timedelta(days=_PRICING_GAP_DAYS)
    settlement = pricing + timedelta(days=_SETTLEMENT_GAP_DAYS)
    first_coupon = settlement + timedelta(days=_FIRST_COUPON_GAP_DAYS)
    return {
        "announcement": announcement,
        "pricing": pricing,
        "settlement": settlement,
        "first_coupon": first_coupon,
    }


def tranche_settlement(series_settlement: date, tranche_index: int) -> date:
    """Series settlement stepped one month per tranche, rolled to a weekday."""
    return _roll_to_weekday(add_months(series_settlement, tranche_index))


def tranche_maturity(announcement: date, tenor_years: int, payment_day: int = PAYMENT_DAY) -> date:
    """Legal final for the class: announcement + tenor, rolled to the payment day."""
    return roll_to_payment_day(add_years(announcement, tenor_years), payment_day)


def ant_redemption_date(
    settlement: date,
    wal_years: float,
    maturity: date,
    payment_day: int = PAYMENT_DAY,
) -> date:
    """Expected call/redemption: settlement + WAL, on a payment day, strictly < maturity."""
    out = roll_to_payment_day(add_years(settlement, wal_years), payment_day)
    while out >= maturity:
        out = roll_to_payment_day(add_months(out, -1) - timedelta(days=1), payment_day)
        if out <= settlement:
            raise ValueError("cannot place ANT_REDEMPTION_DATE between settlement and maturity")
    return out


def series_legal_final(tranche_maturities) -> date:
    """§20.7.1 — the latest tranche maturity plus exactly one day."""
    latest = max(tranche_maturities)
    return latest + timedelta(days=1)


def next_series_announcement(previous: date, rng: random.Random | None = None) -> date:
    """§20.7.3 — series n+1 is announced 1–4 weeks after series n."""
    r = rng or random
    return _roll_to_weekday(previous + timedelta(weeks=r.randint(1, 4)))


def draw_tenor_ladder(count: int, rng: random.Random | None = None) -> list[int]:
    """§20.7.2 — ``count`` tenors from ``TENOR_POOL``, without replacement, ascending."""
    if count > len(TENOR_POOL):
        raise ValueError(f"tenor pool holds {len(TENOR_POOL)} values, asked for {count}")
    r = rng or random
    return sorted(r.sample(list(TENOR_POOL), count))
