"""Security identifiers for the Securitized (ABS) tool — spec §20.8.

Structure (verified against ``backend/tests/fixtures/oakhurst_deal.json``):

    CUSIP        base6 + suffix2 + check          67543M + AA + <cd>
    144A ISIN    "US" + CUSIP9 + check
    Reg S ISIN   "US" + CINS9 + check,  CINS9 = "U" + base5 + suffix2 + <cusip cd>
    EUR/GBP ISIN "XS" + 9 digits + check
    FIGI         "BBG" + 8 alnum + check          (~30% of securities, §20.3.5)

One CUSIP base per **deal**; the 2-character suffix advances AA, AB, AC, … once per
**tranche** in stack order (not per credit class — A-1 and A-2 are separate instruments).

DEVIATION — §20.19.3 golden check digits
----------------------------------------
The sample's check digits are **not** check-digit-valid, so this module does not
reproduce them. ``check_digit`` and ``_isin_check`` were validated against real,
publicly verifiable identifiers (5/5 CUSIPs incl. Tesla ``88160R101``, which has a
letter in the same doubled 6th position as ``67543M``; 5/5 ISINs incl.
``US0378331005`` and ``XS0629974352``) and both are correct. Every sample CUSIP is
exactly +2 off and every sample 144A ISIN exactly +5 off, i.e. the sample's
identifiers were fabricated rather than computed. The corrected values are recorded
in spec §20.19.3 and asserted by ``backend/tests/test_abs_slice1.py``; the *structure*
above is taken from the sample unchanged and is what slice 2 depends on.
"""

from __future__ import annotations

import random
import string

from engines.bonds_engine import _gen_figi, _isin_check
from engines.loan_utils.cusip import check_digit

# CUSIP issue suffixes avoid I and O by market convention (they read as 1 and 0).
_SUFFIX_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"

XS_PREFIX = "XS"
US_PREFIX = "US"
CINS_REGS_LETTER = "U"


def suffix_for(index: int) -> str:
    """0 -> 'AA', 1 -> 'AB', … tranche index to CUSIP issue suffix."""
    if index < 0:
        raise ValueError("suffix index must be >= 0")
    n = len(_SUFFIX_ALPHABET)
    if index >= n * n:
        raise ValueError(f"suffix index {index} exceeds the two-character space")
    return _SUFFIX_ALPHABET[index // n] + _SUFFIX_ALPHABET[index % n]


def gen_cusip_base(rng: random.Random | None = None) -> str:
    """A 6-character CUSIP issuer base shaped like the sample's ``67543M``."""
    r = rng or random
    return "".join(r.choice(string.digits) for _ in range(5)) + r.choice(string.ascii_uppercase)


def cusip(base6: str, tranche_index: int) -> str:
    """``67543M``, 0 -> ``67543MAA<cd>`` (§20.8.1)."""
    if len(base6) != 6:
        raise ValueError("CUSIP base must be exactly 6 characters")
    stem = base6 + suffix_for(tranche_index)
    return stem + str(check_digit(stem))


def cins_regs(base6: str, tranche_index: int) -> str:
    """The Reg S CINS for a US deal: ``U`` + the base's first five + suffix + check."""
    if len(base6) != 6:
        raise ValueError("CUSIP base must be exactly 6 characters")
    stem = CINS_REGS_LETTER + base6[:5] + suffix_for(tranche_index)
    return stem + str(check_digit(stem))


def isin_from_nsin(country: str, nsin9: str) -> str:
    """``US`` + a 9-character national number + ISIN check digit."""
    if len(country) != 2:
        raise ValueError("ISIN country prefix must be 2 characters")
    if len(nsin9) != 9:
        raise ValueError("ISIN national number must be 9 characters")
    root = country.upper() + nsin9.upper()
    return root + _isin_check(root)


def isin_144a(cusip9: str) -> str:
    """144A tranche of a US deal: ``"US" + CUSIP + check`` (§20.8.2)."""
    return isin_from_nsin(US_PREFIX, cusip9)


def isin_regs_us(base6: str, tranche_index: int) -> str:
    """Reg S tranche of a US deal, via the CINS form (§20.8.2)."""
    return isin_from_nsin(US_PREFIX, cins_regs(base6, tranche_index))


def isin_xs(rng: random.Random | None = None) -> str:
    """EUR/GBP: ``XS`` + 9 digits + check (§20.8.2)."""
    r = rng or random
    return isin_from_nsin(XS_PREFIX, "".join(r.choice(string.digits) for _ in range(9)))


def figi() -> str:
    """12-character FIGI via the Bonds generator (§20.8.3)."""
    return _gen_figi()


class IdentifierPool:
    """Run-lifetime uniqueness for CUSIP bases and ISINs (§20.8.4).

    The engine holds one of these for the whole run and redraws on collision, so a
    50-deal run never posts a duplicate identifier.
    """

    def __init__(self, rng: random.Random | None = None, max_attempts: int = 1000):
        self._rng = rng or random
        self._max_attempts = max_attempts
        self.cusip_bases: set[str] = set()
        self.isins: set[str] = set()
        self.figis: set[str] = set()

    def new_cusip_base(self) -> str:
        for _ in range(self._max_attempts):
            base = gen_cusip_base(self._rng)
            if base not in self.cusip_bases:
                self.cusip_bases.add(base)
                return base
        raise RuntimeError("exhausted CUSIP base space")

    def claim_isin(self, value: str) -> str:
        """Register an ISIN, raising if it repeats. Use for the derived (US/CINS) forms."""
        if value in self.isins:
            raise ValueError(f"duplicate ISIN generated: {value}")
        self.isins.add(value)
        return value

    def new_xs_isin(self) -> str:
        for _ in range(self._max_attempts):
            value = isin_xs(self._rng)
            if value not in self.isins:
                self.isins.add(value)
                return value
        raise RuntimeError("exhausted XS ISIN space")

    def new_figi(self) -> str:
        for _ in range(self._max_attempts):
            value = figi()
            if value not in self.figis:
                self.figis.add(value)
                return value
        raise RuntimeError("exhausted FIGI space")
