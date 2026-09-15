"""Pure helpers for the Securitized (ABS) tool — spec §20.7 (dates) and §20.8 (identifiers).

Nothing in here talks to the network or to the log queue; the engine (slice 2) composes
these into a deal. Both modules deliberately *reuse* the existing check-digit and
date primitives rather than reimplementing them:

    engines.loan_utils.cusip.check_digit   -> CUSIP / CINS modulus-10 double-add-double
    engines.bonds_engine._isin_check       -> ISIN check digit
    engines.bonds_engine._gen_figi         -> FIGI
    engines.loan_utils.dates.to_epoch_ms   -> UTC-midnight epoch milliseconds
    engines.loan_utils.dates._roll_to_weekday
"""

from engines.abs_utils import dates, identifiers  # noqa: F401

__all__ = ["dates", "identifiers"]
