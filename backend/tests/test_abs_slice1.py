"""Assertions for ABS slice 1 — reference data + pure utils (spec §20.5, §20.7, §20.8, §20.19.2).

    cd backend
    .\\.venv\\Scripts\\python.exe tests\\test_abs_slice1.py

No pytest, no server, no network — matching the other files in this directory.

Three groups:

* **reference data** — row counts per §20.5.1 and the six vocabulary counts of §20.5.5,
  plus the coherence rules of §20.5.2 that make `entities.csv` worth having.
* **identifiers** — §20.19.3's golden values. The sample's own check digits are *not*
  check-digit-valid, so the goldens below are the corrected ones; the section marked
  "the implementations are correct" proves that with real, publicly verifiable
  identifiers before the corrected values are asserted. See §20.19.3 and
  `engines/abs_utils/identifiers.py`.
* **dates** — §20.7.1 ordering, and the exact epoch values the sample carries.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from engines.abs_utils import dates as D            # noqa: E402
from engines.abs_utils import identifiers as I      # noqa: E402
from engines.bonds_engine import _isin_check        # noqa: E402
from engines.loan_utils.cusip import check_digit    # noqa: E402

REF = BACKEND / "reference" / "securitized"
# Moved out of master/ at §20.18 step 3 — master/ is documentation and this is a
# test fixture. It stays checked in because the §20.19.3 goldens are asserted
# against it.
SAMPLE = BACKEND / "tests" / "fixtures" / "oakhurst_deal.json"

PASS = FAIL = 0


def check(label, got, expected):
    global PASS, FAIL
    if got == expected:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          got      {got!r}\n          expected {expected!r}")


def ok(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def load(name):
    with (REF / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ======================================================================================
print("\n=== reference data — every CSV loads (§20.5.1) ===")

FILES = {
    "entities.csv": None,          # >= 2000, checked below
    "vocabularies.csv": 126,       # DEVIATION: spec says 127 — the workbook holds 56 asset types
    "asset_types.csv": 56,         # DEVIATION: spec says 57
    "currencies.csv": 3,
    "agents.csv": 40,
    "underwriters.csv": 60,
    "legal_advisors.csv": 40,
    "auditors.csv": 10,
    "ratings.csv": 22,
    "analysts.csv": 30,
}
tables = {}
for name, expected_rows in FILES.items():
    tables[name] = load(name)
    if expected_rows is None:
        ok(f"{name} loads ({len(tables[name])} rows)", len(tables[name]) > 0)
    else:
        check(f"{name} row count", len(tables[name]), expected_rows)

check("entities.csv >= 2000 rows", len(tables["entities.csv"]) >= 2000, True)
print(f"        (entities.csv actually holds {len(tables['entities.csv'])} rows)")

expected_columns = {
    "entities.csv": ["ISSUER_NAME", "ISSUER_TICKER", "ISSUER_CIK", "ORIGINATOR",
                     "ORIGINATOR_TICKER", "ORIGINATOR_CIK", "SPONSOR_NAME", "SPONSOR_TICKER",
                     "SPONSOR_CIK", "DEPOSITOR", "SERVICER", "ASSET_FAMILY"],
    "vocabularies.csv": ["FIELD", "VALUE", "WEIGHT"],
    "asset_types.csv": ["ASSET_TYPE", "PRODUCT_GROUP", "INDUSTRY", "SUB_INDUSTRY", "BUSINESS",
                        "PREPAYMENT_TYPE", "PRICING_SPEED", "POOL_PROFILE", "USER_OF_PROCEEDS",
                        "ASSET_FAMILY"],
    "agents.csv": ["NAME", "ROLES"],
    "underwriters.csv": ["NAME", "TICKER"],
    "ratings.csv": ["RANK", "FITCH", "MOODYS", "SP"],
    "analysts.csv": ["EMAIL"],
}
for name, cols in expected_columns.items():
    check(f"{name} columns", list(tables[name][0].keys()), cols)


# ======================================================================================
print("\n=== controlled vocabularies (§20.5.5) ===")

vocab = {}
for row in tables["vocabularies.csv"]:
    vocab.setdefault(row["FIELD"], []).append(row["VALUE"])

# NOTE: ASSET_TYPE is 56 in _source/ABS_DropDowns.xlsx, not the 57 the spec claimed.
EXPECTED_COUNTS = {
    "ASSET_TYPE": 56, "RANKING": 28, "COUPON_TYPE": 17,
    "ACCRUAL_METHOD": 12, "DAY_COUNT": 8, "BUSINESS_DAY_CONVENTION": 5,
}
for field, expected in EXPECTED_COUNTS.items():
    check(f"{field} value count", len(vocab.get(field, [])), expected)

check("no vocabulary field beyond the six", sorted(vocab), sorted(EXPECTED_COUNTS))
for field, values in vocab.items():
    ok(f"{field} has no duplicates", len(values) == len(set(values)))

# §20.5.5 trap 2 — transcribed as-is, not "fixed" to ACT/365.
ok("DAY_COUNT keeps the ACT/36S spelling", "ACT/36S" in vocab["DAY_COUNT"])
ok("DAY_COUNT keeps ACT/36S (FIXED)", "ACT/36S (FIXED)" in vocab["DAY_COUNT"])
ok("DAY_COUNT has no invented ACT/365", "ACT/365" not in vocab["DAY_COUNT"])
ok("DAY_COUNT has no Actual/360", "Actual/360" not in vocab["DAY_COUNT"])
check("DAY_COUNT verbatim", vocab["DAY_COUNT"],
      ["30/360", "30/365", "30/ACT", "ACT/ACT", "ACT/ACT (ICMA)", "ACT/360",
       "ACT/36S", "ACT/36S (FIXED)"])
check("BUSINESS_DAY_CONVENTION verbatim", vocab["BUSINESS_DAY_CONVENTION"],
      ["Following", "Modified Following", "Preceding", "Modified Preceding",
       "No Adjustment (Unadjusted)"])
check("COUPON_TYPE verbatim", vocab["COUPON_TYPE"],
      ["Auction", "Defaulted", "Exchanged", "Fixed", "Fixed to Fixed", "Fixed to Float",
       "Flat Trading", "Float", "Funged", "Hybrid", "OID", "Pay-In-Kind", "Step",
       "Structured", "Tax Credit", "When Issued", "Zero"])

# §20.5.5 — long parenthesised names are exact.
for exact in ["Federal Student Loan (FFELP) ABS", "Buy Now Pay Later (BNPL) ABS",
              "Solar Power Purchase Agreement (PPA) ABS", "Whole Business Securitization (WBS)",
              "Auto Loan ABS", "Re-Performing Loan (RPL) Securitization"]:
    ok(f"ASSET_TYPE contains {exact!r}", exact in vocab["ASSET_TYPE"])

# §20.5.5 trap 3 — v1 generates Fixed and Float; the other 15 are carried at WEIGHT=0.
weights = {(r["FIELD"], r["VALUE"]): int(r["WEIGHT"]) for r in tables["vocabularies.csv"]}
drawable = [v for v in vocab["COUPON_TYPE"] if weights[("COUPON_TYPE", v)] > 0]
check("COUPON_TYPE drawable values", sorted(drawable), ["Fixed", "Float"])

# The sample's values must all be members.
for field, value in [("RANKING", "Senior Secured"), ("RANKING", "Secured"),
                     ("RANKING", "Subordinated"), ("ACCRUAL_METHOD", "Actual balance accrual"),
                     ("DAY_COUNT", "30/360"), ("BUSINESS_DAY_CONVENTION", "Following"),
                     ("COUPON_TYPE", "Fixed"), ("ASSET_TYPE", "Auto Loan ABS")]:
    ok(f"sample {field}={value!r} is a member", value in vocab[field])


# ======================================================================================
print("\n=== asset_types.csv (§20.5.1, §20.5.3) ===")

at = tables["asset_types.csv"]
check("one row per ASSET_TYPE, in vocabulary order",
      [r["ASSET_TYPE"] for r in at], vocab["ASSET_TYPE"])
check("POOL_PROFILE values", sorted({r["POOL_PROFILE"] for r in at}),
      ["commercial", "consumer_auto", "consumer_card", "corporate_credit", "esoteric", "mortgage"])
ok("every row has a non-empty USER_OF_PROCEEDS", all(r["USER_OF_PROCEEDS"] for r in at))
ok("every row has a non-empty BUSINESS", all(r["BUSINESS"] for r in at))
sample_auto = next(r for r in at if r["ASSET_TYPE"] == "Auto Loan ABS")
check("Auto Loan ABS INDUSTRY matches the sample", sample_auto["INDUSTRY"], "Consumer Finance")
check("Auto Loan ABS SUB_INDUSTRY matches the sample", sample_auto["SUB_INDUSTRY"], "Auto Loans")
check("Auto Loan ABS BUSINESS matches the sample", sample_auto["BUSINESS"],
      "Prime retail auto installment loan receivables")
check("Auto Loan ABS PRICING_SPEED matches the sample", sample_auto["PRICING_SPEED"], "1.30% ABS")
check("Auto Loan ABS PREPAYMENT_TYPE matches the sample", sample_auto["PREPAYMENT_TYPE"], "ABS")
check("Auto Loan ABS USER_OF_PROCEEDS matches the sample", sample_auto["USER_OF_PROCEEDS"],
      "Purchase auto receivables from the depositor and fund the reserve account")


# ======================================================================================
print("\n=== entities.csv coherence (§20.5.2) ===")

ents = tables["entities.csv"]
for column in ["ISSUER_NAME", "ORIGINATOR", "SPONSOR_NAME", "DEPOSITOR",
               "ISSUER_TICKER", "ORIGINATOR_TICKER", "SPONSOR_TICKER",
               "ISSUER_CIK", "ORIGINATOR_CIK", "SPONSOR_CIK"]:
    values = [r[column] for r in ents]
    ok(f"{column} is unique across all {len(ents)} rows",
       len(set(values)) == len(values),
       f"{len(values) - len(set(values))} duplicates")

ok(">= 2000 distinct issuers", len({r["ISSUER_NAME"] for r in ents}) >= 2000)
ok(">= 2000 distinct originators", len({r["ORIGINATOR"] for r in ents}) >= 2000)
ok(">= 2000 distinct sponsors", len({r["SPONSOR_NAME"] for r in ents}) >= 2000)

families = {r["ASSET_FAMILY"] for r in ents}
check("ASSET_FAMILY values", sorted(families),
      ["auto", "card", "commercial", "consumer", "corporate_credit", "equipment",
       "esoteric", "mortgage", "student"])
check("every ASSET_FAMILY is used by asset_types.csv",
      sorted(families), sorted({r["ASSET_FAMILY"] for r in at}))

# The four names are one corporate family: they share a brand stem.
mismatched = [r for r in ents
              if not (r["ORIGINATOR"].startswith(r["ISSUER_NAME"].split()[0])
                      and r["SPONSOR_NAME"].startswith(r["ISSUER_NAME"].split()[0])
                      and r["DEPOSITOR"].startswith(r["ISSUER_NAME"].split()[0]))]
ok("originator/sponsor/depositor share the issuer's brand stem", not mismatched,
   f"{len(mismatched)} rows do not")

# §20.5.2 — the shelf stem carries no vintage; the engine appends it.
ok("no ISSUER_NAME carries a vintage",
   not [r for r in ents if any(part[:2] == "20" and "-" in part for part in r["ISSUER_NAME"].split())])
ok("no ISSUER_TICKER carries a vintage suffix",
   all(r["ISSUER_TICKER"].isalpha() or r["ISSUER_TICKER"][-1].isdigit() for r in ents))

# §20.5.2 [A] — SERVICER == ORIGINATOR unless overridden; ~10% carry a third party.
third_party = [r for r in ents if r["SERVICER"] != r["ORIGINATOR"]]
share = len(third_party) / len(ents)
ok(f"~10% third-party servicers (actual {share:.1%})", 0.05 <= share <= 0.15)

# Row 0 is the sample's own family — names, tickers and CIKs (§20.5.2's worked example).
sample = json.loads(SAMPLE.read_text(encoding="utf-8"))["DETAILS"] if SAMPLE.exists() else None
row0 = ents[0]
check("row 0 ISSUER_NAME (shelf stem)", row0["ISSUER_NAME"], "Oakhurst Auto Receivables Trust")
check("row 0 ISSUER_TICKER (stem)", row0["ISSUER_TICKER"], "OART")
check("row 0 ORIGINATOR", row0["ORIGINATOR"], "Oakhurst Auto Finance LLC")
check("row 0 ORIGINATOR_TICKER", row0["ORIGINATOR_TICKER"], "OAKAF")
check("row 0 SPONSOR_NAME", row0["SPONSOR_NAME"], "Oakhurst Capital Markets LLC")
check("row 0 SPONSOR_TICKER", row0["SPONSOR_TICKER"], "OAKCM")
check("row 0 DEPOSITOR", row0["DEPOSITOR"], "Oakhurst Auto Receivables Depositor LLC")
check("row 0 SERVICER == ORIGINATOR", row0["SERVICER"], row0["ORIGINATOR"])
if sample:
    check("row 0 ISSUER_CIK == sample", row0["ISSUER_CIK"], str(sample["ISSUER_CIK"]))
    check("row 0 ORIGINATOR_CIK == sample", row0["ORIGINATOR_CIK"], str(sample["ORIGINATOR_CIK"]))
    check("row 0 SPONSOR_CIK == sample", row0["SPONSOR_CIK"], str(sample["SPONSOR_CIK"]))
    check("row 0 ORIGINATOR == sample", row0["ORIGINATOR"], sample["ORIGINATOR"])
    check("row 0 SPONSOR_NAME == sample", row0["SPONSOR_NAME"], sample["SPONSOR_NAME"])
    check("row 0 DEPOSITOR == sample", row0["DEPOSITOR"], sample["DEPOSITOR"])
    check("row 0 ISSUER_TICKER + vintage == sample",
          row0["ISSUER_TICKER"] + "261", sample["ISSUER_TICKER"])
    check("row 0 ISSUER_NAME + vintage == sample",
          row0["ISSUER_NAME"] + " 2026-1", sample["ISSUER_NAME"])


# ======================================================================================
print("\n=== currencies / agents / ratings ===")

ccy = {r["CODE"]: r for r in tables["currencies.csv"]}
check("currencies", sorted(ccy), ["EUR", "GBP", "USD"])
check("USD ISIN prefix", ccy["USD"]["ISIN_PREFIX"], "US")
check("USD reg types", ccy["USD"]["DEFAULT_REG_TYPES"], "144A|Reg S")
check("EUR reg types (Reg S only)", ccy["EUR"]["DEFAULT_REG_TYPES"], "Reg S")
check("GBP reg types (Reg S only)", ccy["GBP"]["DEFAULT_REG_TYPES"], "Reg S")
check("EUR/GBP ISIN prefix is XS",
      (ccy["EUR"]["ISIN_PREFIX"], ccy["GBP"]["ISIN_PREFIX"]), ("XS", "XS"))
check("EUR countries are euro-area (§20.5.4 RESOLVED)",
      ccy["EUR"]["COUNTRY"].split("|"), ["ES", "IT", "FR", "DE", "NL", "IE"])
ok("EUR COUNTRY and COUNTRY_NAME are index-aligned",
   len(ccy["EUR"]["COUNTRY"].split("|")) == len(ccy["EUR"]["COUNTRY_NAME"].split("|")))
ok("EUR FLOAT_BENCHMARK and INDEX_LEVEL are index-aligned",
   len(ccy["EUR"]["FLOAT_BENCHMARK"].split("|")) == len(ccy["EUR"]["INDEX_LEVEL"].split("|")))
check("USD benchmark", ccy["USD"]["FLOAT_BENCHMARK"], "SOFR")
check("GBP benchmark", ccy["GBP"]["FLOAT_BENCHMARK"], "SONIA")
check("USD governing law matches the sample", ccy["USD"]["GOVERNING_LAW"], "NY Law")
check("USD listing matches the sample", ccy["USD"]["LISTING_EXCHANGE"], "Unlisted")
# DEVIATION: DAY_COUNT is a currencies.csv column (§20.5.4 makes it currency-driven,
# §20.5.1's column list omits it) and uses the vocabulary's spellings.
for code in ("USD", "EUR", "GBP"):
    ok(f"{code} DAY_COUNT is a vocabulary member ({ccy[code]['DAY_COUNT']})",
       ccy[code]["DAY_COUNT"] in vocab["DAY_COUNT"])

roles = {"trustee", "paying_agent", "registrar", "calculation_agent", "custodian",
         "backup_servicer"}
bad_roles = {r for row in tables["agents.csv"] for r in row["ROLES"].split("|")} - roles
ok("agents.csv uses only the six roles", not bad_roles, f"unexpected: {bad_roles}")
for role in sorted(roles):
    ok(f"at least one agent can act as {role}",
       any(role in row["ROLES"].split("|") for row in tables["agents.csv"]))

ratings = tables["ratings.csv"]
check("ratings.csv RANK is 1..22 in order",
      [int(r["RANK"]) for r in ratings], list(range(1, 23)))
check("ratings.csv top notch", (ratings[0]["FITCH"], ratings[0]["MOODYS"], ratings[0]["SP"]),
      ("AAA", "Aaa", "AAA"))
by_fitch = {r["FITCH"]: r for r in ratings}
# The sample steps AAA/Aaa -> AA/Aa2 -> BBB/Baa2; all three pairs must sit on the ladder.
for f, m, s in [("AAA", "Aaa", "AAA"), ("AA", "Aa2", "AA"), ("BBB", "Baa2", "BBB")]:
    ok(f"sample notch {f}/{m}/{s} is on the ladder",
       f in by_fitch and by_fitch[f]["MOODYS"] == m and by_fitch[f]["SP"] == s)

ok("every analyst is an email address",
   all("@" in r["EMAIL"] for r in tables["analysts.csv"]))
ok("the sample's analysts are present",
   {"miso.park@troweprice.com", "john.tempco@troweprice.com"}
   <= {r["EMAIL"] for r in tables["analysts.csv"]})


# ======================================================================================
print("\n=== identifiers: the implementations are correct (§20.8) ===")
# Before asserting corrected goldens, prove check_digit and _isin_check against real,
# publicly verifiable identifiers. Tesla's 88160R101 matters most: it carries a letter in
# the same doubled 6th position as the sample's 67543M.
for base, expected, name in [("03783310", "0", "Apple 037833100"),
                             ("59491810", "4", "Microsoft 594918104"),
                             ("88160R10", "1", "Tesla 88160R101"),
                             ("02079K30", "5", "Alphabet C 02079K305"),
                             ("46625H10", "0", "JPMorgan 46625H100")]:
    check(f"CUSIP check digit — {name}", str(check_digit(base)), expected)

for isin, name in [("US0378331005", "Apple"), ("US5949181045", "Microsoft"),
                   ("GB0002634946", "BAE Systems"), ("DE0005557508", "Deutsche Telekom"),
                   ("XS0629974352", "a XS eurobond")]:
    check(f"ISIN check digit — {name} {isin}", _isin_check(isin[:11]), isin[11])


print("\n=== identifiers: §20.19.3 golden values (CORRECTED — see deviation note) ===")
# The sample's own digits are 67543MAA4 / AB2 / AC0 and US67543MAA48 / AB21, and
# USU67543AA55 / AB39. Every CUSIP is exactly +2 off and every 144A ISIN exactly +5 off
# the correct value, i.e. the sample's identifiers were fabricated, not computed.
BASE = "67543M"
check("CUSIP base 67543M, suffix AA", I.cusip(BASE, 0), "67543MAA2")
check("CUSIP base 67543M, suffix AB", I.cusip(BASE, 1), "67543MAB0")
check("CUSIP base 67543M, suffix AC", I.cusip(BASE, 2), "67543MAC8")
check("144A ISIN from 67543MAA2", I.isin_144a(I.cusip(BASE, 0)), "US67543MAA27")
check("144A ISIN from 67543MAB0", I.isin_144a(I.cusip(BASE, 1)), "US67543MAB00")
check("Reg S ISIN, base 67543, suffix AA", I.isin_regs_us(BASE, 0), "USU67543AA38")
check("Reg S ISIN, base 67543, suffix AB", I.isin_regs_us(BASE, 1), "USU67543AB11")

print("\n--- and the structure the sample does fix (which is what slice 2 uses) ---")
SAMPLE_144A = ["67543MAA4", "67543MAB2", "67543MAC0"]
SAMPLE_ISIN = ["US67543MAA48", "US67543MAB21", "US67543MAC04"]
SAMPLE_REGS = ["USU67543AA55", "USU67543AB39", "USU67543AC12"]
for i in range(3):
    c = I.cusip(BASE, i)
    check(f"CUSIP[{i}] shares the sample's base and suffix", c[:8], SAMPLE_144A[i][:8])
    # First 10 only: char 11 is the CUSIP check digit, which is where the sample is wrong.
    check(f"144A ISIN[{i}] shares the sample's first 10", I.isin_144a(c)[:10], SAMPLE_ISIN[i][:10])
    r = I.isin_regs_us(BASE, i)
    check(f"Reg S ISIN[{i}] shares the sample's first 10", r[:10], SAMPLE_REGS[i][:10])
    check(f"CUSIP[{i}] length", len(c), 9)
    check(f"144A ISIN[{i}] length", len(I.isin_144a(c)), 12)
    check(f"Reg S ISIN[{i}] length", len(r), 12)

check("suffixes advance per tranche", [I.suffix_for(i) for i in range(4)],
      ["AA", "AB", "AC", "AD"])
ok("suffix alphabet skips I and O", "I" not in I._SUFFIX_ALPHABET and "O" not in I._SUFFIX_ALPHABET)

xs = I.isin_xs()
check("XS ISIN length", len(xs), 12)
check("XS ISIN prefix", xs[:2], "XS")
check("XS ISIN check digit is self-consistent", _isin_check(xs[:11]), xs[11])
fig = I.figi()
check("FIGI length", len(fig), 12)
check("FIGI prefix", fig[:3], "BBG")

pool = I.IdentifierPool()
bases = {pool.new_cusip_base() for _ in range(500)}
check("IdentifierPool yields 500 distinct CUSIP bases", len(bases), 500)
check("IdentifierPool yields 200 distinct XS ISINs",
      len({pool.new_xs_isin() for _ in range(200)}), 200)
pool.claim_isin("US0378331005")
try:
    pool.claim_isin("US0378331005")
    rejected = False
except ValueError:
    rejected = True
ok("IdentifierPool rejects a duplicate ISIN", rejected)


# ======================================================================================
print("\n=== dates (§20.7) ===")

ANNOUNCEMENT = date(2026, 8, 5)          # the sample's ANNOUNCEMENT_DT
sd = D.series_dates(ANNOUNCEMENT)

check("ANNOUNCEMENT_DT epoch", D.to_epoch_ms(sd["announcement"]), 1785888000000)
check("EXPECTED_PRICING_DATE epoch", D.to_epoch_ms(sd["pricing"]), 1786492800000)
check("SETTLEMENT_DATE epoch", D.to_epoch_ms(sd["settlement"]), 1786924800000)
check("FIRST_COUPON_DT epoch", D.to_epoch_ms(sd["first_coupon"]), 1789603200000)
ok("§20.7.1 ordering announcement < pricing < settlement < first coupon",
   sd["announcement"] < sd["pricing"] < sd["settlement"] < sd["first_coupon"])
check("pricing is +7d", (sd["pricing"] - sd["announcement"]).days, 7)
check("settlement is +5d", (sd["settlement"] - sd["pricing"]).days, 5)
check("first coupon is +31d", (sd["first_coupon"] - sd["settlement"]).days, 31)

# §20.19.3 — tranche tenors 3Y/5Y/6Y land at announcement + 1106/1836/2202 days.
TENORS = [3, 5, 6]
mats = [D.tranche_maturity(ANNOUNCEMENT, t) for t in TENORS]
check("tenor day-counts from announcement", [(m - ANNOUNCEMENT).days for m in mats],
      [1106, 1836, 2202])
check("tranche MATURITY_DATE epochs match the sample",
      [D.to_epoch_ms(m) for m in mats], [1881446400000, 1944518400000, 1976140800000])
ok("tranche maturities ascend with the tenor ladder", mats == sorted(mats))

legal_final = D.series_legal_final(mats)
check("series legal final epoch matches the sample", D.to_epoch_ms(legal_final), 1976227200000)
check("series legal final - latest tranche maturity",
      D.to_epoch_ms(legal_final) - max(D.to_epoch_ms(m) for m in mats), 86400000)
check("that gap is exactly one day", D.ONE_DAY_MS, 86400000)

settles = [D.tranche_settlement(sd["settlement"], i) for i in range(3)]
check("TRANCHE_SETTLEMENT_DATE epochs match the sample",
      [D.to_epoch_ms(s) for s in settles], [1786924800000, 1789603200000, 1792368000000])
ok("every tranche settles on or after the series settlement",
   all(s >= sd["settlement"] for s in settles))
ok("no tranche settles on a weekend", all(s.weekday() < 5 for s in settles))

WALS = [1.40, 3.2, 3.9]
ants = [D.ant_redemption_date(settles[i], WALS[i], mats[i]) for i in range(3)]
ok("ANT_REDEMPTION_DATE < MATURITY_DATE for every tranche",
   all(ants[i] < mats[i] for i in range(3)),
   f"{[str(a) for a in ants]} vs {[str(m) for m in mats]}")
ok("ANT_REDEMPTION_DATE > settlement for every tranche",
   all(ants[i] > settles[i] for i in range(3)))
ok("ANT_REDEMPTION_DATE falls on the payment day",
   all(a.day == D.PAYMENT_DAY for a in ants), f"{[str(a) for a in ants]}")

# §20.7.2 — tenors ascend, without replacement, from the pool.
import random as _random                                                    # noqa: E402
for n in range(1, len(D.TENOR_POOL) + 1):
    ladder = D.draw_tenor_ladder(n, _random.Random(n))
    ok(f"tenor ladder of {n} ascends without replacement",
       ladder == sorted(ladder) and len(set(ladder)) == n
       and set(ladder) <= set(D.TENOR_POOL))

# §20.7.3 — series n+1 is announced 1-4 weeks after series n.
for seed in range(20):
    nxt = D.next_series_announcement(ANNOUNCEMENT, _random.Random(seed))
    gap = (nxt - ANNOUNCEMENT).days
    ok(f"series n+1 announcement is 1-4 weeks later (seed {seed}, +{gap}d)",
       7 <= gap <= 30 and nxt.weekday() < 5)

# Leap-day safety for the year arithmetic.
check("2028-02-29 + 1Y clamps to 2029-02-28",
      D.add_years(date(2028, 2, 29), 1), date(2029, 2, 28))
check("roll_to_payment_day is never backwards",
      D.roll_to_payment_day(date(2026, 8, 20)), date(2026, 9, 15))
check("roll_to_payment_day keeps the 15th", D.roll_to_payment_day(date(2026, 8, 15)),
      date(2026, 8, 15))


# ======================================================================================
print(f"\n{'='*70}\n{PASS} passed, {FAIL} failed\n{'='*70}")
sys.exit(1 if FAIL else 0)
