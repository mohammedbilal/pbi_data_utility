import random
from datetime import date, datetime, timedelta, timezone


def _roll_to_weekday(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _advance(d, min_gap=3, max_gap=5):
    return _roll_to_weekday(d + timedelta(days=random.randint(min_gap, max_gap)))


def to_epoch_ms(d):
    dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def generate_deal_dates(start=None):
    announcement = _roll_to_weekday((start or date.today()) + timedelta(days=random.randint(3, 5)))
    meeting = _advance(announcement)
    commit = _advance(meeting)
    price = _advance(commit)
    return {
        "ANNOUNCEMENT_DT": to_epoch_ms(announcement),
        "MEETING_DATE": to_epoch_ms(meeting),
        "COMMIT_DATE": to_epoch_ms(commit),
        "PRICE_DATE": to_epoch_ms(price),
    }, price


def generate_tranche_dates(price_date, tenor_years):
    settlement = _advance(price_date)
    maturity = _roll_to_weekday(settlement + timedelta(days=int(round(tenor_years * 365.25))))
    return {
        "TRANCHE_SETTLEMENT_DATE": to_epoch_ms(settlement),
        "MATURITY_DATE": to_epoch_ms(maturity),
    }
