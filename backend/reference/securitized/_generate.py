"""One-off builder for `backend/reference/securitized/` — spec §20.5.

    cd backend
    .\\.venv\\Scripts\\python.exe reference\\securitized\\_generate.py

Deterministic (fixed seed), idempotent, and **checked in with its output**. The CSVs are
the durable artifact; this script exists so the data can be regenerated or extended
without hand-editing 2,160 rows. It is never called at runtime — the engine only reads
the CSVs, and every file is optional with a hardcoded fallback (§5.4 / §6.5 / §20.5).

Sources
-------
* `_source/ABS_DropDowns.xlsx`, sheet `Drop-down Lists` -> `vocabularies.csv`, transcribed
  **verbatim**, including the `ACT/36S` misspelling (§20.5.5 trap 2). If the workbook is
  absent the vocabulary step is skipped and the checked-in `vocabularies.csv` is left
  alone. It lived in `master/` until §20.18 step 3 **moved** it here — deleting it, as an
  earlier draft of that step said, would silently void the byte-stable-regeneration
  guarantee this script exists to provide.
* `backend/tests/fixtures/oakhurst_deal.json` — the shape of every value here matches that
  payload: row 0 of `entities.csv` *is* the Oakhurst family, tickers and CIKs included.
  (Also moved out of `master/` at §20.18 step 3.)

Deviations from the spec as written — see §20.5.5 / §20.5.1 / §20.5.4, all recorded there
--------------------------------------------------------------------------------------
1. **56 asset types, not 57.** The workbook holds 56 values under `Asset Type`
   (rows 2-57 of a 137-row sheet). `vocabularies.csv` therefore has 126 rows, not 127.
   The spec's counts were off by one; the workbook is the authority and is transcribed
   as-is.
2. **`currencies.csv` gains a `DAY_COUNT` column.** §20.5.4 makes day count
   currency-driven but §20.5.1's column list omits it and there is nowhere else to put
   it. Its values use the workbook's spellings: `30/360` (USD), `ACT/360` (EUR) and
   `ACT/36S` (GBP) — §20.5.4's `Actual/360` / `Actual/365` do not exist in the
   vocabulary, and `ACT/36S` is the platform's (misspelled) ACT/365.
3. **`PRODUCT_GROUP` is `ABS` on 51 of 56 rows.** *(revised slice 3, 2026-08-13.)* The five
   asset types that name their own product group — RMBS, CMBS, CLO, CDO, CBO — carry it;
   everything else keeps the sample's `ABS`. The live probe (§20.13 step 7.7) ACKed
   `PRODUCT_GROUP: "RMBS"`, but its negative control ACKed `"ZZ_NOT_A_PRODUCT_GROUP"` too:
   ingest validates property *names* and types, never string *values*. So this is a
   data-quality choice, not a schema finding — see `PRODUCT_GROUP_OVERRIDES` below.
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORKBOOK = HERE / "_source" / "ABS_DropDowns.xlsx"
SHEET = "Drop-down Lists"

SEED = 20260813
ENTITY_TARGET = 2000

# The sample's own CIKs: originator 1701455, sponsor +5, issuer +17. Row 0 reproduces them.
CIK_BASE = 1_701_455
CIK_ORIGINATOR_OFFSET = 0
CIK_SPONSOR_OFFSET = 5
CIK_ISSUER_OFFSET = 17
CIK_STRIDE = 24

THIRD_PARTY_SERVICER_EVERY = 10  # §20.5.2 [A] — ~10% of rows carry a third-party servicer


# --------------------------------------------------------------------------------------
# vocabularies.csv — §20.5.5
# --------------------------------------------------------------------------------------

# Header text in column A -> the FIELD name the engine reads. Order follows the workbook.
_VOCAB_HEADERS = {
    "Asset Type": "ASSET_TYPE",
    "Accrual method": "ACCRUAL_METHOD",
    "Ranking/Seniority": "RANKING",
    "Day Count Convention": "DAY_COUNT",
    "Business Day Conventions": "BUSINESS_DAY_CONVENTION",
    "Coupon Type": "COUPON_TYPE",
}

_EXPECTED_VOCAB_COUNTS = {
    "ASSET_TYPE": 56,   # spec says 57; the workbook says 56 — see deviation 1 above
    "RANKING": 28,
    "COUPON_TYPE": 17,
    "ACCRUAL_METHOD": 12,
    "DAY_COUNT": 8,
    "BUSINESS_DAY_CONVENTION": 5,
}

# §20.5.5 trap 3 — v1 generates Fixed and Float only; the other 15 are carried at WEIGHT=0
# so enabling one later is a data change plus a generator branch.
_COUPON_TYPE_WEIGHTS = {"Fixed": 6, "Float": 4}

# Bank-capital and insurance rankings are valid platform values but are not ABS rankings,
# so they are carried at WEIGHT=0 rather than dropped.
_RANKING_WEIGHTS = {
    "Senior Secured": 8, "Secured": 5, "Subordinated": 6, "Junior Subordinated": 3,
    "Senior Subordinated": 3, "Asset Backed": 4, "1st Lien": 3, "2nd Lien": 2,
    "3rd Lien": 1, "Unsecured": 2, "Preferred": 1,
}

# PIK and Z-tranche accrual imply coupon types v1 does not generate (§20.5.5 trap 3).
_ACCRUAL_METHOD_WEIGHTS = {
    "Actual balance accrual": 6, "Monthly accrual": 5, "Scheduled balance accrual": 4,
    "Simple interest accrual": 4, "Daily accrual": 2, "Discount accretion": 1,
    "Premium amortization": 1, "Deferred interest accrual": 1,
    "Interest shortfall carryforward": 1, "Excess spread accrual / release": 1,
    "PIK / capitalized interest": 0, "Accrual bond / Z-tranche method": 0,
}

# Day count is currency-driven (§20.5.4); these weights are only the fallback ordering.
_DAY_COUNT_WEIGHTS = {"30/360": 5, "ACT/360": 4, "ACT/36S": 3, "ACT/ACT": 2}

_BDC_WEIGHTS = {"Following": 6, "Modified Following": 4, "Preceding": 1,
                "Modified Preceding": 1, "No Adjustment (Unadjusted)": 1}

_VOCAB_WEIGHTS = {
    "COUPON_TYPE": (_COUPON_TYPE_WEIGHTS, 0),
    "RANKING": (_RANKING_WEIGHTS, 0),
    "ACCRUAL_METHOD": (_ACCRUAL_METHOD_WEIGHTS, 1),
    "DAY_COUNT": (_DAY_COUNT_WEIGHTS, 1),
    "BUSINESS_DAY_CONVENTION": (_BDC_WEIGHTS, 1),
}


def read_workbook_vocabularies() -> list[tuple[str, str]]:
    """(FIELD, VALUE) pairs in workbook order. Requires openpyxl and the workbook."""
    import openpyxl

    wb = openpyxl.load_workbook(WORKBOOK, data_only=True)
    ws = wb[SHEET]
    out: list[tuple[str, str]] = []
    field = None
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True):
        raw = row[0]
        value = str(raw).strip() if raw is not None else ""
        if not value:
            field = None
            continue
        if value in _VOCAB_HEADERS:
            field = _VOCAB_HEADERS[value]
            continue
        if field is None:
            raise ValueError(f"value {value!r} appears before any recognised header")
        out.append((field, value))          # verbatim — no case, spelling or spacing fixes
    return out


def write_vocabularies(path: Path) -> dict[str, int]:
    pairs = read_workbook_vocabularies()
    rows = []
    for field, value in pairs:
        table, default = _VOCAB_WEIGHTS.get(field, ({}, None))
        if field == "ASSET_TYPE":
            weight = ASSET_TYPE_WEIGHT[value]
        else:
            weight = table.get(value, default if default is not None else 1)
        rows.append({"FIELD": field, "VALUE": value, "WEIGHT": weight})
    _write_csv(path, ["FIELD", "VALUE", "WEIGHT"], rows)
    counts: dict[str, int] = {}
    for field, _ in pairs:
        counts[field] = counts.get(field, 0) + 1
    return counts


# --------------------------------------------------------------------------------------
# asset_types.csv — §20.5.1, §20.5.3
# --------------------------------------------------------------------------------------
# (ASSET_TYPE, FAMILY, POOL_PROFILE, INDUSTRY, SUB_INDUSTRY, BUSINESS,
#  PREPAYMENT_TYPE, PRICING_SPEED, COLLATERAL, WEIGHT)
#
# COLLATERAL fills the sample's USER_OF_PROCEEDS template:
#   "Purchase {collateral} from the depositor and fund the reserve account"
# PRICING_SPEED is a representative quote; the engine varies the numeric part and keeps
# the convention suffix (the sample's Auto Loan ABS tranches all read "1.30% ABS").
ASSET_TYPES: list[tuple] = [
    # --- auto -------------------------------------------------------------------------
    ("Auto Loan ABS", "auto", "consumer_auto", "Consumer Finance", "Auto Loans",
     "Prime retail auto installment loan receivables", "ABS", "1.30% ABS",
     "auto receivables", 10),
    ("Auto Lease ABS", "auto", "consumer_auto", "Consumer Finance", "Auto Leases",
     "Retail automobile lease contracts and residual values", "ABS", "1.20% ABS",
     "auto lease contracts", 7),
    ("Dealer Floorplan ABS", "auto", "commercial", "Commercial Finance", "Dealer Floorplan",
     "Revolving wholesale inventory financing lines to franchised dealers", "MPR", "28% MPR",
     "dealer floorplan receivables", 5),
    ("Fleet Lease ABS", "auto", "commercial", "Commercial Finance", "Fleet Leasing",
     "Commercial fleet vehicle leases and service contracts", "ABS", "1.10% ABS",
     "fleet lease receivables", 3),
    ("Recreational Vehicle (RV) ABS", "auto", "consumer_auto", "Consumer Finance",
     "Recreational Vehicles", "Retail recreational vehicle installment contracts",
     "ABS", "1.00% ABS", "recreational vehicle receivables", 2),
    ("Marine (Boat) ABS", "auto", "consumer_auto", "Consumer Finance", "Marine Lending",
     "Retail marine installment loan receivables", "ABS", "0.90% ABS",
     "marine loan receivables", 2),
    ("Powersports ABS", "auto", "consumer_auto", "Consumer Finance", "Powersports",
     "Retail powersports installment contracts", "ABS", "1.40% ABS",
     "powersports receivables", 2),
    ("Motorcycle ABS", "auto", "consumer_auto", "Consumer Finance", "Motorcycle Lending",
     "Retail motorcycle installment loan receivables", "ABS", "1.35% ABS",
     "motorcycle receivables", 2),
    # --- card -------------------------------------------------------------------------
    ("Credit Card ABS", "card", "consumer_card", "Consumer Finance", "Credit Cards",
     "Revolving general purpose credit card receivables", "MPR", "22% MPR",
     "credit card receivables", 10),
    ("Point-of-Sale Financing ABS", "card", "consumer_card", "Consumer Finance",
     "Point-of-Sale Credit", "Installment point-of-sale credit receivables",
     "MPR", "26% MPR", "point-of-sale receivables", 4),
    ("Buy Now Pay Later (BNPL) ABS", "card", "consumer_card", "Consumer Finance",
     "Buy Now Pay Later", "Short-duration instalment buy-now-pay-later receivables",
     "MPR", "45% MPR", "BNPL receivables", 4),
    ("Device Payment Plan ABS", "card", "consumer_card", "Consumer Finance",
     "Device Financing", "Handset and device instalment payment plan receivables",
     "ABS", "1.50% ABS", "device payment plan receivables", 3),
    # --- consumer ---------------------------------------------------------------------
    ("Unsecured Consumer Loan ABS", "consumer", "consumer_card", "Consumer Finance",
     "Unsecured Consumer Loans", "Fixed-rate unsecured consumer instalment loans",
     "CPR", "14% CPR", "consumer loan receivables", 8),
    ("Marketplace Loan ABS", "consumer", "consumer_card", "Consumer Finance",
     "Marketplace Lending", "Platform-originated unsecured consumer instalment loans",
     "CPR", "16% CPR", "marketplace loan receivables", 5),
    ("Timeshare ABS", "consumer", "esoteric", "Consumer Finance", "Timeshare",
     "Vacation ownership interval loan receivables", "CPR", "16% CPR",
     "timeshare loan receivables", 3),
    ("Structured Settlement ABS", "consumer", "esoteric", "Specialty Finance",
     "Structured Settlements", "Purchased structured settlement payment streams",
     "None", "0% CPR", "structured settlement receivables", 2),
    ("Life Settlement ABS", "consumer", "esoteric", "Specialty Finance", "Life Settlements",
     "Purchased life insurance policies and premium reserves", "None", "0% CPR",
     "life settlement policies", 1),
    ("Litigation Finance ABS", "consumer", "esoteric", "Specialty Finance",
     "Litigation Finance", "Advances against contingent legal claim recoveries",
     "None", "0% CPR", "litigation finance advances", 1),
    # --- student ----------------------------------------------------------------------
    ("Federal Student Loan (FFELP) ABS", "student", "consumer_card", "Consumer Finance",
     "Student Loans", "Government-guaranteed FFELP student loan receivables",
     "CPR", "6% CPR", "FFELP student loan receivables", 5),
    ("Private Student Loan ABS", "student", "consumer_card", "Consumer Finance",
     "Student Loans", "Cosigned private student loan receivables", "CPR", "8% CPR",
     "private student loan receivables", 5),
    # --- equipment --------------------------------------------------------------------
    ("Equipment Lease ABS", "equipment", "commercial", "Commercial Finance",
     "Equipment Leasing", "Small-ticket commercial equipment leases and loans",
     "ABS", "1.10% ABS", "equipment lease receivables", 8),
    ("Small Business Loan ABS", "equipment", "commercial", "Commercial Finance",
     "Small Business Lending", "Secured small business term loan receivables",
     "CPR", "10% CPR", "small business loan receivables", 4),
    ("Trade Receivables ABS", "equipment", "commercial", "Commercial Finance",
     "Trade Receivables", "Revolving short-dated corporate trade receivables",
     "MPR", "80% MPR", "trade receivables", 4),
    ("Insurance Premium Finance ABS", "equipment", "commercial", "Specialty Finance",
     "Premium Finance", "Commercial insurance premium finance receivables",
     "MPR", "35% MPR", "premium finance receivables", 2),
    ("Franchise Loan ABS", "equipment", "commercial", "Commercial Finance",
     "Franchise Lending", "Secured loans to multi-unit franchise operators",
     "CPR", "6% CPR", "franchise loan receivables", 2),
    ("Recurring Revenue (SaaS) ABS", "equipment", "commercial", "Specialty Finance",
     "Recurring Revenue Lending", "Advances against contracted software subscription revenue",
     "CPR", "12% CPR", "recurring revenue contracts", 1),
    # --- mortgage ---------------------------------------------------------------------
    ("Residential Mortgage-Backed Securities (RMBS)", "mortgage", "mortgage",
     "Mortgage Finance", "Residential Mortgages",
     "First-lien residential mortgage loans", "CPR", "8% CPR",
     "residential mortgage loans", 9),
    ("Home Equity Line of Credit (HELOC) ABS", "mortgage", "mortgage", "Mortgage Finance",
     "Home Equity", "Revolving second-lien home equity lines of credit", "CPR", "18% CPR",
     "home equity lines of credit", 4),
    ("Manufactured Housing ABS", "mortgage", "mortgage", "Mortgage Finance",
     "Manufactured Housing", "Chattel and land-home manufactured housing loans",
     "CPR", "6% CPR", "manufactured housing loans", 2),
    ("Single-Family Rental (SFR) ABS", "mortgage", "commercial", "Mortgage Finance",
     "Single-Family Rental", "Loans secured by portfolios of single-family rental homes",
     "None", "0% CPR", "single-family rental loans", 3),
    ("Non-Performing Loan (NPL) Securitization", "mortgage", "mortgage", "Mortgage Finance",
     "Non-Performing Loans", "Seasoned non-performing residential mortgage loans",
     "CPR", "12% CPR", "non-performing mortgage loans", 3),
    ("Re-Performing Loan (RPL) Securitization", "mortgage", "mortgage", "Mortgage Finance",
     "Re-Performing Loans", "Modified re-performing residential mortgage loans",
     "CPR", "10% CPR", "re-performing mortgage loans", 3),
    ("Mortgage Servicing Rights (MSR) ABS", "mortgage", "esoteric", "Mortgage Finance",
     "Servicing Rights", "Advances against mortgage servicing rights cashflows",
     "CPR", "10% CPR", "mortgage servicing rights", 2),
    ("Servicer Advance ABS", "mortgage", "esoteric", "Mortgage Finance",
     "Servicer Advances", "Reimbursable principal, interest and escrow servicer advances",
     "MPR", "40% MPR", "servicer advance receivables", 2),
    # --- commercial -------------------------------------------------------------------
    ("Commercial Mortgage-Backed Securities (CMBS)", "commercial", "commercial",
     "Commercial Real Estate", "Commercial Mortgages",
     "First-lien commercial mortgage loans on stabilised properties", "None", "0% CPR",
     "commercial mortgage loans", 9),
    ("Data Center ABS", "commercial", "commercial", "Digital Infrastructure", "Data Centers",
     "Long-term data centre colocation and hyperscale leases", "None", "0% CPR",
     "data centre lease revenues", 4),
    ("Cell Tower ABS", "commercial", "commercial", "Digital Infrastructure",
     "Wireless Towers", "Wireless tower site lease revenues", "None", "0% CPR",
     "tower site lease revenues", 3),
    ("Fiber Network ABS", "commercial", "commercial", "Digital Infrastructure",
     "Fiber Networks", "Contracted fibre network access and dark fibre revenues",
     "None", "0% CPR", "fibre network revenues", 3),
    ("Toll Road ABS", "commercial", "commercial", "Infrastructure", "Toll Roads",
     "Toll road concession revenues", "None", "0% CPR", "toll road revenues", 2),
    ("Infrastructure Revenue ABS", "commercial", "commercial", "Infrastructure",
     "Infrastructure Revenues", "Availability and usage-based infrastructure revenues",
     "None", "0% CPR", "infrastructure revenues", 2),
    ("Utility Tariff (Rate Reduction) ABS", "commercial", "commercial", "Utilities",
     "Securitized Utility Tariffs", "Statutorily authorised utility tariff charges",
     "None", "0% CPR", "utility tariff property", 2),
    ("Tobacco Settlement ABS", "commercial", "esoteric", "Public Finance",
     "Tobacco Settlements", "Master Settlement Agreement tobacco payment streams",
     "None", "0% CPR", "tobacco settlement revenues", 1),
    # --- corporate_credit -------------------------------------------------------------
    ("Collateralized Loan Obligation (CLO)", "corporate_credit", "corporate_credit",
     "Structured Credit", "Broadly Syndicated Loans",
     "Diversified pool of first-lien broadly syndicated corporate loans", "CPR", "20% CPR",
     "broadly syndicated corporate loans", 8),
    ("Collateralized Debt Obligation (CDO)", "corporate_credit", "corporate_credit",
     "Structured Credit", "Structured Credit",
     "Diversified pool of structured credit obligations", "CPR", "15% CPR",
     "structured credit obligations", 3),
    ("Collateralized Bond Obligation (CBO)", "corporate_credit", "corporate_credit",
     "Structured Credit", "Corporate Bonds", "Diversified pool of corporate bonds",
     "CPR", "12% CPR", "corporate bond collateral", 2),
    ("Collateralized Fund Obligation (CFO)", "corporate_credit", "corporate_credit",
     "Structured Credit", "Fund Interests",
     "Diversified pool of private fund limited partnership interests", "None", "0% CPR",
     "private fund interests", 2),
    # --- esoteric ---------------------------------------------------------------------
    ("Aircraft Lease ABS", "esoteric", "esoteric", "Transportation", "Aircraft Leasing",
     "Portfolio of commercial aircraft operating leases", "None", "0% CPR",
     "aircraft and lease receivables", 5),
    ("Shipping Container ABS", "esoteric", "esoteric", "Transportation",
     "Container Leasing", "Portfolio of marine shipping container leases", "None", "0% CPR",
     "container lease receivables", 3),
    ("Railcar Lease ABS", "esoteric", "esoteric", "Transportation", "Railcar Leasing",
     "Portfolio of railcar operating leases", "None", "0% CPR",
     "railcar lease receivables", 3),
    ("Solar Loan ABS", "esoteric", "esoteric", "Renewable Energy", "Residential Solar",
     "Residential solar equipment loan receivables", "CPR", "6% CPR",
     "solar loan receivables", 4),
    ("Solar Power Purchase Agreement (PPA) ABS", "esoteric", "esoteric", "Renewable Energy",
     "Solar PPAs", "Residential solar power purchase agreements and leases", "CPR", "5% CPR",
     "solar power purchase agreements", 3),
    ("Property Assessed Clean Energy (PACE) ABS", "esoteric", "esoteric", "Renewable Energy",
     "PACE Assessments", "Property assessed clean energy tax assessments", "CPR", "4% CPR",
     "PACE assessments", 2),
    ("Music Royalty ABS", "esoteric", "esoteric", "Media and Entertainment",
     "Music Royalties", "Catalogue music publishing and recorded royalties", "None", "0% CPR",
     "music royalty streams", 2),
    ("Film and Media Royalty ABS", "esoteric", "esoteric", "Media and Entertainment",
     "Film Libraries", "Film and television library distribution revenues", "None", "0% CPR",
     "film library revenues", 1),
    ("Pharmaceutical Royalty ABS", "esoteric", "esoteric", "Healthcare", "Drug Royalties",
     "Contracted pharmaceutical product royalty streams", "None", "0% CPR",
     "pharmaceutical royalty streams", 2),
    ("Whole Business Securitization (WBS)", "esoteric", "esoteric", "Specialty Finance",
     "Whole Business", "Franchise royalty, supply chain and IP cashflows of an operating business",
     "None", "0% CPR", "franchise and royalty cashflows", 3),
]

ASSET_TYPE_WEIGHT = {row[0]: row[9] for row in ASSET_TYPES}
# deviation 3 (revised slice 3, 2026-08-13) — `ABS` for everything except the six
# asset types that *name* their own product group. Verified live: the handler ACKs
# `PRODUCT_GROUP: "RMBS"`, but it also ACKs `"ZZ_NOT_A_PRODUCT_GROUP"`, so the ACK is
# not evidence the value is recognised — ingest validates property names and types,
# never string values (§20.5.5). This is therefore a data-quality choice, not a
# schema finding: `PRODUCT_GROUP: "ABS"` on an RMBS deal is exactly the
# plausible-but-wrong data §20.6 exists to prevent. Reverting is a one-line edit.
PRODUCT_GROUP_DEFAULT = "ABS"
PRODUCT_GROUP_OVERRIDES = {
    "Residential Mortgage-Backed Securities (RMBS)": "RMBS",
    "Commercial Mortgage-Backed Securities (CMBS)": "CMBS",
    "Collateralized Loan Obligation (CLO)": "CLO",
    "Collateralized Debt Obligation (CDO)": "CDO",
    "Collateralized Bond Obligation (CBO)": "CBO",
}
# Deliberately NOT overridden: Collateralized Fund Obligation (CFO) — market usage puts
# it inside the CDO family rather than giving it a group of its own, so a guess here
# would be fabrication. The mortgage-adjacent NPL/RPL securitizations stay `ABS` for the
# same reason: they are not marketed as RMBS.
USER_OF_PROCEEDS_TEMPLATE = "Purchase {collateral} from the depositor and fund the reserve account"


def _vocabulary_asset_type_order(path: Path) -> list[str]:
    """ASSET_TYPE values in workbook order, read back from the vocabularies CSV."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [r["VALUE"] for r in csv.DictReader(fh) if r["FIELD"] == "ASSET_TYPE"]


def write_asset_types(path: Path, order: list[str] | None = None) -> int:
    """One row per ASSET_TYPE. Emitted in workbook order so the two files diff cleanly."""
    table = list(ASSET_TYPES)
    if order:
        rank = {name: i for i, name in enumerate(order)}
        missing = [row[0] for row in table if row[0] not in rank]
        if missing:
            raise SystemExit(f"asset types absent from the vocabulary: {missing}")
        if len(rank) != len(table):
            raise SystemExit(f"vocabulary has {len(rank)} asset types, table has {len(table)}")
        table.sort(key=lambda row: rank[row[0]])

    rows = []
    for name, family, profile, industry, sub, business, prepay, speed, collateral, _w in table:
        rows.append({
            "ASSET_TYPE": name,
            "PRODUCT_GROUP": PRODUCT_GROUP_OVERRIDES.get(name, PRODUCT_GROUP_DEFAULT),
            "INDUSTRY": industry,
            "SUB_INDUSTRY": sub,
            "BUSINESS": business,
            "PREPAYMENT_TYPE": prepay,
            "PRICING_SPEED": speed,
            "POOL_PROFILE": profile,
            "USER_OF_PROCEEDS": USER_OF_PROCEEDS_TEMPLATE.format(collateral=collateral),
            "ASSET_FAMILY": family,
        })
    _write_csv(path, list(rows[0].keys()), rows)
    return len(rows)


# --------------------------------------------------------------------------------------
# entities.csv — §20.5.2
# --------------------------------------------------------------------------------------
# One row per corporate family. ORIGINATOR / SPONSOR / DEPOSITOR / ISSUER share a brand,
# so a generated deal reads like a real one instead of three unrelated names.
#
# FAMILIES: key -> (originator suffix, originator ticker suffix, sponsor suffix,
#                   sponsor ticker suffix, depositor suffix, issuer shelf suffix,
#                   issuer ticker fallback letter)
FAMILIES: dict[str, tuple] = {
    "auto": ("Auto Finance LLC", "AF", "Capital Markets LLC", "CM",
             "Auto Receivables Depositor LLC", "Auto Receivables Trust", "A"),
    "card": ("Card Services LLC", "CS", "Payments Holdings LLC", "PH",
             "Card Funding LLC", "Credit Card Master Trust", "C"),
    "consumer": ("Consumer Finance LLC", "CF", "Consumer Capital LLC", "CC",
                 "Consumer Receivables Depositor LLC", "Consumer Loan Trust", "U"),
    "student": ("Student Lending LLC", "SL", "Education Capital LLC", "EC",
                "Student Funding LLC", "Student Loan Trust", "S"),
    "equipment": ("Equipment Finance LLC", "EF", "Leasing Holdings LLC", "LH",
                  "Equipment Receivables Depositor LLC", "Equipment Lease Trust", "E"),
    "mortgage": ("Mortgage Company LLC", "MC", "Residential Capital LLC", "RC",
                 "Mortgage Depositor LLC", "Mortgage Trust", "M"),
    "commercial": ("Commercial Realty Capital LLC", "RE", "Commercial Capital LLC", "KC",
                   "Commercial Mortgage Depositor LLC", "Commercial Mortgage Trust", "K"),
    "corporate_credit": ("Asset Management LLC", "AM", "Credit Partners LLC", "CP",
                         "Credit Funding LLC", "Corporate Credit Trust", "P"),
    "esoteric": ("Specialty Finance LLC", "SF", "Structured Capital LLC", "SC",
                 "Specialty Funding LLC", "Specialty Asset Trust", "R"),
}

# Oakhurst is first so row 0 of entities.csv reproduces tests/fixtures/oakhurst_deal.json
# exactly — names, tickers and CIKs. It is the cheapest possible regression test.
BRANDS: list[str] = [
    "Oakhurst", "Brightline", "Cascadia", "Ironwood", "Silverpeak", "Northvale",
    "Redstone", "Bluecrest", "Harborview", "Stonebridge", "Fairmount", "Larkspur",
    "Windermere", "Ashgrove", "Cedarpoint", "Kestrel", "Meridian", "Alderwood",
    "Blackford", "Clearwater", "Dunmore", "Eastgate", "Fallbrook", "Glenmoor",
    "Havenbrook", "Inglewood", "Juniper", "Kirkland", "Lakeshore", "Marbury",
    "Newbridge", "Orchardton", "Pinehurst", "Quarrymill", "Rosemont", "Summit",
    "Thornbury", "Underhill", "Valemont", "Westmark", "Yellowstone", "Zephyr",
    "Amberfield", "Bayridge", "Coppervale", "Deerfield", "Elmwood", "Foxglove",
    "Granitepoint", "Hillcrest", "Ivybridge", "Jasperfield", "Kingsley", "Linden",
    "Millbrook", "Norwood", "Oakvale", "Parkmont", "Queensbury", "Riverton",
    "Stanhope", "Templeton", "Uplands", "Vermilion", "Waverly", "Ashcombe",
    "Birchmere", "Carrington", "Dovercourt", "Everly", "Fenwick", "Greystone",
    "Hollowbrook", "Inverness", "Jamesport", "Kelburn", "Longmeadow", "Merribrook",
    "Nightingale", "Overton", "Pemberton", "Quimby", "Ravenswood", "Stillwater",
    "Trentham", "Ullswater", "Vinehill", "Whitfield", "Ambleside", "Braxton",
    "Cheshire", "Danforth", "Edgewater", "Fairhaven", "Glenbrook", "Hartwell",
    "Ingram", "Jarvis", "Kentmere", "Lyndhurst", "Montrose", "Northgate",
    "Ollerton", "Prescott", "Quayside", "Rockvale", "Sedgewick", "Tanglewood",
    "Uxbridge", "Vandermeer", "Westbourne", "Ashford", "Belmont", "Colchester",
    "Draycott", "Ellesmere", "Fairweather", "Garrison", "Hexham", "Ilkley",
    "Jedburgh", "Kenmore", "Lancaster", "Marlowe", "Northfield", "Oakmere",
    "Pendleton", "Queenscliff", "Rothwell", "Selkirk", "Tregarth", "Ulverston",
    "Verwood", "Wexford", "Aldridge", "Bramwell", "Cranleigh", "Dunsmore",
    "Eastvale", "Fordham", "Glenrock", "Hazelmere", "Ipswich", "Jorvik",
    "Kirkstone", "Lambourne", "Middleton", "Netherby", "Oldbury", "Padstow",
    "Quenby", "Redmarsh", "Stroud", "Thirlmere", "Ustonbury", "Valeworth",
    "Wenlock", "Ashbourne", "Berkeley", "Cobham", "Denby", "Eltham",
    "Frampton", "Gladstone", "Henley", "Islington", "Jesmond", "Kelmscott",
    "Ludlow", "Mowbray", "Newquay", "Osterley", "Petworth", "Quorndon",
    "Radlett", "Saltburn", "Tiverton", "Upminster", "Vauxhall", "Warkworth",
    "Alnwick", "Bexley", "Chorley", "Dalkeith", "Earlsfield", "Fairlight",
    "Gosforth", "Hessle", "Ingleby", "Jervaulx", "Knaresborough", "Lostwithiel",
    "Malvern", "Nantwich", "Ormskirk", "Prestbury", "Quantock", "Rothbury",
    "Sandbach", "Tarporley", "Ulceby", "Vowchurch", "Wilmslow", "Ackworth",
    "Bicester", "Chalfont", "Datchet", "Elstree", "Frinton", "Godalming",
    "Harpenden", "Ivinghoe", "Jordans", "Kimpton", "Letchworth", "Marlow",
    "Northwood", "Otley", "Pinner", "Quainton", "Rickmansworth", "Sandridge",
    "Tring", "Uxendon", "Verulam", "Wendover", "Aylesbury", "Beaconsfield",
    "Chesham", "Denham", "Eton", "Farnham", "Gerrards", "Haddenham",
    "Iver", "Jarrow", "Kidlington", "Lechlade", "Minster", "Newbury",
    "Oakley", "Princes", "Quenington", "Ramsbury", "Stow", "Thame",
    "Uffington", "Ventnor", "Wallingford", "Amersham",
]

THIRD_PARTY_SERVICERS = [
    "Vervent Inc.", "Systems & Services Technologies, Inc.", "Nationstar Mortgage LLC",
    "Select Portfolio Servicing, Inc.", "Shellpoint Mortgage Servicing",
    "Rushmore Loan Management Services LLC", "Mr. Cooper Group Inc.",
    "Computershare Loan Services", "Situs Asset Management LLC", "Midland Loan Services",
    "KeyBank Real Estate Capital", "Wells Fargo Commercial Mortgage Servicing",
    "PHH Mortgage Corporation", "Statebridge Company LLC", "Cenlar FSB",
]


def _unique_code(name: str, taken: set[str], length: int = 3) -> str:
    """A short, unique, stable uppercase code for a brand."""
    letters = [c for c in name.upper() if c.isalpha()]
    candidates = [
        "".join(letters[:length]),
        letters[0] + "".join(letters[2:2 + length - 1]),
        letters[0] + "".join(letters[3:3 + length - 1]),
        letters[0] + letters[1] + letters[-1],
        letters[0] + letters[-2] + letters[-1],
    ]
    for candidate in candidates:
        if len(candidate) == length and candidate not in taken:
            taken.add(candidate)
            return candidate
    stem = "".join(letters[:length - 1])
    for suffix in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        candidate = stem + suffix
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise RuntimeError(f"cannot allocate a unique code for {name!r}")


def _initials(text: str, cap: int = 4) -> str:
    return "".join(word[0] for word in text.split() if word)[:cap].upper()


def build_entities() -> list[dict]:
    rng = random.Random(SEED)
    brand_codes: set[str] = set()
    codes = {brand: _unique_code(brand, brand_codes) for brand in BRANDS}

    issuer_tickers: set[str] = set()
    rows: list[dict] = []
    for brand in BRANDS:
        code = codes[brand]
        for family, spec in FAMILIES.items():
            (orig_suffix, orig_tick, sponsor_suffix, sponsor_tick,
             depositor_suffix, shelf_suffix, family_letter) = spec

            originator = f"{brand} {orig_suffix}"
            sponsor = f"{brand} {sponsor_suffix}"
            depositor = f"{brand} {depositor_suffix}"
            shelf = f"{brand} {shelf_suffix}"

            # Prefer the shelf's initials (Oakhurst Auto Receivables Trust -> OART, as in
            # the sample); fall back to the brand's unique code + a family letter.
            ticker = _initials(shelf)
            if ticker in issuer_tickers:
                ticker = code + family_letter
                bump = 0
                while ticker in issuer_tickers:
                    bump += 1
                    ticker = f"{code}{family_letter}{bump}"
            issuer_tickers.add(ticker)

            idx = len(rows)
            cik = CIK_BASE + idx * CIK_STRIDE
            servicer = originator
            if idx % THIRD_PARTY_SERVICER_EVERY == THIRD_PARTY_SERVICER_EVERY - 1:
                servicer = rng.choice(THIRD_PARTY_SERVICERS)

            rows.append({
                "ISSUER_NAME": shelf,                      # shelf stem — no vintage (§20.5.2)
                "ISSUER_TICKER": ticker,                   # stem — engine appends e.g. "261"
                "ISSUER_CIK": cik + CIK_ISSUER_OFFSET,
                "ORIGINATOR": originator,
                "ORIGINATOR_TICKER": code + orig_tick,
                "ORIGINATOR_CIK": cik + CIK_ORIGINATOR_OFFSET,
                "SPONSOR_NAME": sponsor,
                "SPONSOR_TICKER": code + sponsor_tick,
                "SPONSOR_CIK": cik + CIK_SPONSOR_OFFSET,
                "DEPOSITOR": depositor,
                "SERVICER": servicer,
                "ASSET_FAMILY": family,
            })
    return rows


def write_entities(path: Path) -> int:
    rows = build_entities()
    if len(rows) < ENTITY_TARGET:
        raise SystemExit(f"entities.csv would hold {len(rows)} rows, need >= {ENTITY_TARGET}")
    _write_csv(path, list(rows[0].keys()), rows)
    return len(rows)


# --------------------------------------------------------------------------------------
# currencies.csv — §20.5.4
# --------------------------------------------------------------------------------------
# Multi-valued cells are pipe-separated; COUNTRY and COUNTRY_NAME are index-aligned, as
# are FLOAT_BENCHMARK and INDEX_LEVEL.
CURRENCIES = [
    {
        "CODE": "USD",
        "COUNTRY": "US",
        "COUNTRY_NAME": "United States",
        "FLOAT_BENCHMARK": "SOFR",
        "INDEX_LEVEL": "4.33",
        "CLEARING_SYSTEM": "DTC|Euroclear",
        "DEPOSITORY": "DTC|Euroclear / Clearstream",
        "ISIN_PREFIX": "US",
        "GOVERNING_LAW": "NY Law",
        "LISTING_EXCHANGE": "Unlisted",
        "DEFAULT_REG_TYPES": "144A|Reg S",
        "DAY_COUNT": "30/360",
    },
    {
        "CODE": "EUR",
        "COUNTRY": "ES|IT|FR|DE|NL|IE",
        "COUNTRY_NAME": "Spain|Italy|France|Germany|Netherlands|Ireland",
        "FLOAT_BENCHMARK": "EURIBOR 3M|ESTR",
        "INDEX_LEVEL": "2.35|2.15",
        "CLEARING_SYSTEM": "Euroclear|Clearstream",
        "DEPOSITORY": "Euroclear / Clearstream",
        "ISIN_PREFIX": "XS",
        "GOVERNING_LAW": "Irish Law|Dutch Law|English Law",
        "LISTING_EXCHANGE": "Euronext Dublin|LuxSE",
        "DEFAULT_REG_TYPES": "Reg S",
        "DAY_COUNT": "ACT/360",
    },
    {
        "CODE": "GBP",
        "COUNTRY": "GB",
        "COUNTRY_NAME": "United Kingdom",
        "FLOAT_BENCHMARK": "SONIA",
        "INDEX_LEVEL": "4.20",
        "CLEARING_SYSTEM": "Euroclear|Clearstream",
        "DEPOSITORY": "Euroclear / Clearstream",
        "ISIN_PREFIX": "XS",
        "GOVERNING_LAW": "English Law",
        "LISTING_EXCHANGE": "London Stock Exchange",
        "DEFAULT_REG_TYPES": "Reg S",
        "DAY_COUNT": "ACT/36S",
    },
]


# --------------------------------------------------------------------------------------
# agents / underwriters / advisors / auditors / ratings / analysts
# --------------------------------------------------------------------------------------
# ROLES is pipe-separated from
# trustee|paying_agent|registrar|calculation_agent|custodian|backup_servicer
_ALL_AGENT_ROLES = "trustee|paying_agent|registrar|calculation_agent|custodian|backup_servicer"
AGENTS = [
    ("Wilmington Trust, N.A.", "trustee|custodian|backup_servicer"),
    ("Citibank, N.A.", _ALL_AGENT_ROLES),
    ("Deutsche Bank Trust Company Americas", _ALL_AGENT_ROLES),
    ("U.S. Bank Trust Company, National Association", _ALL_AGENT_ROLES),
    ("The Bank of New York Mellon", _ALL_AGENT_ROLES),
    ("Computershare Trust Company, N.A.", "trustee|paying_agent|registrar|backup_servicer"),
    ("Wells Fargo Bank, N.A.", "trustee|paying_agent|custodian"),
    ("Regions Bank", "trustee|paying_agent|registrar"),
    ("BOKF, NA", "trustee|paying_agent"),
    ("UMB Bank, N.A.", "trustee|paying_agent|registrar"),
    ("Wilmington Savings Fund Society, FSB", "trustee|custodian"),
    ("TMI Trust Company", "trustee|backup_servicer"),
    ("GLAS Trust Company LLC", "trustee|calculation_agent"),
    ("Delaware Trust Company", "trustee"),
    ("Argent Institutional Trust Company", "trustee|paying_agent"),
    ("Wilmington Trust (London) Limited", "trustee|registrar"),
    ("BNP Paribas Trust Corporation UK Limited", "trustee|paying_agent|registrar"),
    ("HSBC Bank plc", "paying_agent|registrar|custodian"),
    ("Elavon Financial Services DAC", "paying_agent|registrar|custodian"),
    ("Citibank, N.A., London Branch", "paying_agent|registrar|calculation_agent"),
    ("The Bank of New York Mellon, London Branch", "paying_agent|registrar|custodian"),
    ("Deutsche Bank AG, London Branch", "paying_agent|calculation_agent"),
    ("Banco Santander S.A.", "paying_agent|custodian"),
    ("Intertrust Trustees Limited", "trustee"),
    ("CSC Capital Markets UK Limited", "trustee|registrar"),
    ("Ocorian Corporate Services (UK) Limited", "trustee"),
    ("Apex Corporate Trust Services", "trustee|calculation_agent"),
    ("Vistra Corporate Services", "trustee|registrar"),
    ("Sanne Group plc", "calculation_agent"),
    ("Virtus Group, LP", "calculation_agent|backup_servicer"),
    ("Trimont Real Estate Advisors", "calculation_agent|backup_servicer"),
    ("Situs Asset Management LLC", "backup_servicer"),
    ("Vervent Inc.", "backup_servicer"),
    ("Systems & Services Technologies, Inc.", "backup_servicer"),
    ("Portfolio Financial Servicing Company", "backup_servicer"),
    ("Midland Loan Services", "backup_servicer|calculation_agent"),
    ("Computershare Loan Services", "backup_servicer"),
    ("State Street Bank and Trust Company", "custodian|paying_agent"),
    ("Northern Trust Company", "custodian"),
    ("BNP Paribas Securities Services", "custodian|registrar"),
]

UNDERWRITERS = [
    ("J.P. Morgan Securities LLC", "JPM"), ("BofA Securities, Inc.", "BAC"),
    ("Citigroup Global Markets Inc.", "C"), ("Wells Fargo Securities, LLC", "WFC"),
    ("Goldman Sachs & Co. LLC", "GS"), ("Morgan Stanley & Co. LLC", "MS"),
    ("Barclays Capital Inc.", "BARC"), ("Deutsche Bank Securities Inc.", "DB"),
    ("Credit Agricole Securities (USA) Inc.", "ACA"), ("BNP Paribas Securities Corp.", "BNP"),
    ("RBC Capital Markets, LLC", "RY"), ("TD Securities (USA) LLC", "TD"),
    ("BMO Capital Markets Corp.", "BMO"), ("Scotia Capital (USA) Inc.", "BNS"),
    ("CIBC World Markets Corp.", "CM"), ("SMBC Nikko Securities America, Inc.", "SMFG"),
    ("Mizuho Securities USA LLC", "MFG"), ("MUFG Securities Americas Inc.", "MUFG"),
    ("Nomura Securities International, Inc.", "NMR"), ("SG Americas Securities, LLC", "GLE"),
    ("Natixis Securities Americas LLC", "KN"), ("Santander US Capital Markets LLC", "SAN"),
    ("UBS Securities LLC", "UBS"), ("HSBC Securities (USA) Inc.", "HSBC"),
    ("Jefferies LLC", "JEF"), ("Guggenheim Securities, LLC", "GUGG"),
    ("Stifel, Nicolaus & Company, Incorporated", "SF"), ("Raymond James & Associates, Inc.", "RJF"),
    ("Piper Sandler & Co.", "PIPR"), ("KeyBanc Capital Markets Inc.", "KEY"),
    ("Truist Securities, Inc.", "TFC"), ("Fifth Third Securities, Inc.", "FITB"),
    ("Regions Securities LLC", "RF"), ("PNC Capital Markets LLC", "PNC"),
    ("US Bancorp Investments, Inc.", "USB"), ("Citizens JMP Securities, LLC", "CFG"),
    ("Huntington Securities, Inc.", "HBAN"), ("Comerica Securities, Inc.", "CMA"),
    ("BNY Mellon Capital Markets, LLC", "BK"), ("Academy Securities, Inc.", "ACAD"),
    ("Siebert Williams Shank & Co., LLC", "SWS"), ("Loop Capital Markets LLC", "LOOP"),
    ("R. Seelaus & Co., LLC", "SEEL"), ("Ramirez & Co., Inc.", "RAMZ"),
    ("Blaylock Van, LLC", "BLAY"), ("CastleOak Securities, L.P.", "COAK"),
    ("Mischler Financial Group, Inc.", "MISC"), ("Great Pacific Securities", "GPAC"),
    ("Amherst Pierpont Securities LLC", "AMPS"), ("Cantor Fitzgerald & Co.", "CANT"),
    ("StoneX Financial Inc.", "SNEX"), ("Oppenheimer & Co. Inc.", "OPY"),
    ("B. Riley Securities, Inc.", "RILY"), ("Brean Capital, LLC", "BREA"),
    ("Performance Trust Capital Partners, LLC", "PTCP"), ("Multi-Bank Securities, Inc.", "MBSI"),
    ("Wedbush Securities Inc.", "WEDB"), ("Janney Montgomery Scott LLC", "JNNY"),
    ("Lloyds Securities Inc.", "LLOY"), ("NatWest Markets Securities Inc.", "NWG"),
]

LEGAL_ADVISORS = [
    "Mayer Brown LLP", "Sidley Austin LLP", "Katten Muchin Rosenman LLP",
    "Dechert LLP", "Cadwalader, Wickersham & Taft LLP", "Orrick, Herrington & Sutcliffe LLP",
    "Hunton Andrews Kurth LLP", "Chapman and Cutler LLP", "Alston & Bird LLP",
    "Morgan, Lewis & Bockius LLP", "Skadden, Arps, Slate, Meagher & Flom LLP",
    "Davis Polk & Wardwell LLP", "Simpson Thacher & Bartlett LLP", "Cleary Gottlieb Steen & Hamilton LLP",
    "Latham & Watkins LLP", "Kirkland & Ellis LLP", "Weil, Gotshal & Manges LLP",
    "Paul Hastings LLP", "Milbank LLP", "Allen Overy Shearman Sterling LLP",
    "Clifford Chance LLP", "Linklaters LLP", "Freshfields LLP", "Ashurst LLP",
    "Hogan Lovells International LLP", "Norton Rose Fulbright LLP", "Baker McKenzie LLP",
    "White & Case LLP", "Reed Smith LLP", "DLA Piper LLP", "Eversheds Sutherland LLP",
    "Arthur Cox LLP", "Matheson LLP", "A&L Goodbody LLP", "McCann FitzGerald LLP",
    "NautaDutilh N.V.", "Loyens & Loeff N.V.", "Uria Menendez Abogados",
    "Chiomenti Studio Legale", "Gide Loyrette Nouel A.A.R.P.I.",
]

AUDITORS = [
    "Deloitte & Touche LLP", "Ernst & Young LLP", "KPMG LLP",
    "PricewaterhouseCoopers LLP", "Grant Thornton LLP", "BDO USA, P.C.",
    "RSM US LLP", "Crowe LLP", "Baker Tilly US, LLP", "Mazars USA LLP",
]

# One row per notch, most senior first (§20.5.1). The sample's AAA/Aaa, AA/Aa2 and
# BBB/Baa2 all sit on this ladder.
RATINGS = [
    ("AAA", "Aaa", "AAA"), ("AA+", "Aa1", "AA+"), ("AA", "Aa2", "AA"), ("AA-", "Aa3", "AA-"),
    ("A+", "A1", "A+"), ("A", "A2", "A"), ("A-", "A3", "A-"),
    ("BBB+", "Baa1", "BBB+"), ("BBB", "Baa2", "BBB"), ("BBB-", "Baa3", "BBB-"),
    ("BB+", "Ba1", "BB+"), ("BB", "Ba2", "BB"), ("BB-", "Ba3", "BB-"),
    ("B+", "B1", "B+"), ("B", "B2", "B"), ("B-", "B3", "B-"),
    ("CCC+", "Caa1", "CCC+"), ("CCC", "Caa2", "CCC"), ("CCC-", "Caa3", "CCC-"),
    ("CC", "Ca", "CC"), ("C", "C", "C"), ("D", "C", "D"),
]

# The sample's two analysts lead the list.
ANALYSTS = [
    "miso.park", "john.tempco", "sarah.whelan", "david.okonkwo", "priya.raman",
    "michael.brennan", "elena.kovacs", "tomas.lindqvist", "rachel.adeyemi", "daniel.fitzgerald",
    "naomi.tanaka", "carlos.mendes", "aisha.rahman", "gregory.paulson", "hannah.mcgrath",
    "vikram.desai", "laura.bianchi", "stefan.mueller", "olivia.hartley", "jerome.baptiste",
    "chloe.dubois", "marcus.eriksen", "fatima.zahra", "peter.nakamura", "isabel.moreno",
    "andrew.callahan", "yuki.shimizu", "beatrice.hollins", "samuel.oyelaran", "clara.novak",
]
ANALYST_DOMAIN = "troweprice.com"


# --------------------------------------------------------------------------------------

def _write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    print(f"writing reference data to {HERE}")

    if WORKBOOK.exists():
        counts = write_vocabularies(HERE / "vocabularies.csv")
        total = sum(counts.values())
        print(f"  vocabularies.csv    {total} rows  {counts}")
        for field, expected in _EXPECTED_VOCAB_COUNTS.items():
            got = counts.get(field, 0)
            flag = "ok" if got == expected else "MISMATCH"
            print(f"      {field:26s} {got:3d}  (expected {expected})  {flag}")
    else:
        print(f"  vocabularies.csv    SKIPPED — {WORKBOOK} not found; keeping the checked-in file")

    order = _vocabulary_asset_type_order(HERE / "vocabularies.csv")
    print(f"  asset_types.csv     {write_asset_types(HERE / 'asset_types.csv', order)} rows"
          f"{'' if order else '  (table order — no vocabularies.csv to sort by)'}")
    print(f"  entities.csv        {write_entities(HERE / 'entities.csv')} rows")

    _write_csv(HERE / "currencies.csv", list(CURRENCIES[0].keys()), CURRENCIES)
    print(f"  currencies.csv      {len(CURRENCIES)} rows")

    _write_csv(HERE / "agents.csv", ["NAME", "ROLES"],
               [{"NAME": n, "ROLES": r} for n, r in AGENTS])
    print(f"  agents.csv          {len(AGENTS)} rows")

    _write_csv(HERE / "underwriters.csv", ["NAME", "TICKER"],
               [{"NAME": n, "TICKER": t} for n, t in UNDERWRITERS])
    print(f"  underwriters.csv    {len(UNDERWRITERS)} rows")

    _write_csv(HERE / "legal_advisors.csv", ["NAME"], [{"NAME": n} for n in LEGAL_ADVISORS])
    print(f"  legal_advisors.csv  {len(LEGAL_ADVISORS)} rows")

    _write_csv(HERE / "auditors.csv", ["NAME"], [{"NAME": n} for n in AUDITORS])
    print(f"  auditors.csv        {len(AUDITORS)} rows")

    _write_csv(HERE / "ratings.csv", ["RANK", "FITCH", "MOODYS", "SP"],
               [{"RANK": i, "FITCH": f, "MOODYS": m, "SP": s}
                for i, (f, m, s) in enumerate(RATINGS, 1)])
    print(f"  ratings.csv         {len(RATINGS)} rows")

    _write_csv(HERE / "analysts.csv", ["EMAIL"],
               [{"EMAIL": f"{name}@{ANALYST_DOMAIN}"} for name in ANALYSTS])
    print(f"  analysts.csv        {len(ANALYSTS)} rows")

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
