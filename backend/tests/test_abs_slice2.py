"""Dry-run assertions for ABS slice 2 — the engine and its payloads (spec §20.19.2, §20.19.4).

    cd backend
    .\\.venv\\Scripts\\python.exe tests\\test_abs_slice2.py

No pytest, no server, no network — matching the other files in this directory. The engine
is driven exactly as `CLAUDE.md` prescribes for an engine smoke test: a real
``queue.Queue`` + ``threading.Event`` with ``dry_run: True``, then the emitted payloads are
pulled back off the queue and asserted.

The six shapes are §20.19.4's. Each shape is run REPEATS times, because the generator is
deliberately unseeded (§20.19.2) — a single pass would only prove one draw is consistent,
and the ladder-monotonicity rules of §20.6.4 are exactly the kind that hold for most draws.

Assertions recompute the §20.6 arithmetic *independently* rather than calling the engine's
own ``check_payload``; that checker is then run as well and required to be silent, so a bug
in the checker cannot hide a bug in the generator (and vice versa).
"""
from __future__ import annotations

import csv
import json
import os
import queue
import re
import sys
import threading
from collections import defaultdict
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from engines import securitized_engine as E          # noqa: E402

REF = BACKEND / "reference" / "securitized"

#: Raise it to stress the unseeded ladders harder: ABS_SLICE2_REPEATS=60 python tests\...
REPEATS = int(os.environ.get("ABS_SLICE2_REPEATS", "12"))

PASS = FAIL = 0
FAILURES = []


def ok(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{label}  {detail}")
        print(f"  FAIL  {label}  {detail}")


def check(label, got, expected):
    ok(label, got == expected, f"got {got!r}, expected {expected!r}")


def group(title):
    print(f"\n=== {title}")


def load_csv(name):
    with (REF / name).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


VOCAB = defaultdict(set)
for _row in load_csv("vocabularies.csv"):
    VOCAB[_row["FIELD"]].add(_row["VALUE"])

CURRENCIES = {r["CODE"]: r for r in load_csv("currencies.csv")}


# ── harness ────────────────────────────────────────────────────────────────────

def run_engine(**params):
    """Drive ``run_securitized`` in dry-run and return (payloads, log_lines, summary)."""
    q = queue.Queue()
    stop = threading.Event()
    p = {"ref_dir": str(REF), "dry_run": True, "delay": 0}
    p.update(params)
    E.run_securitized(p, {"host_name": "unused", "verify_ssl": False}, stop, q)

    logs, summary = [], None
    while True:
        item = q.get()
        if item is None:
            break
        if item.get("type") == "summary":
            summary = item
        else:
            logs.append(item)

    payloads = []
    for item in logs:
        msg = item["msg"]
        if msg.startswith("{"):
            payloads.append(json.loads(msg))
    return payloads, logs, summary


def tranches_of(series):
    """Tranches in TEMP-Tranches-<n> order — the stack order the generator built."""
    return [series["TRANCHES"][k] for k in sorted(series["TRANCHES"], key=lambda s: int(s.rsplit("-", 1)[1]))]


def series_of(details):
    return [details["SERIES"][k] for k in sorted(details["SERIES"], key=lambda s: int(s.rsplit("-", 1)[1]))]


# ── per-payload assertions ─────────────────────────────────────────────────────

MANDATORY_DEAL = ("ORIGINATOR", "ISSUER_NAME", "ISSUER_TICKER", "ISSUER_CIK", "CURRENCY_CODE")
MANDATORY_SERIES = ("SERIES_NAME", "CURRENCY_CODE", "MATURITY_DATE")
MANDATORY_TRANCHE = ("TRANCHE_CLASS", "CCY", "TENOR", "COUPON_TYPE", "MATURITY_DATE")
MANDATORY_SECURITY = ("REG_TYPE",)

STRING_FIELDS = {"ISSUER_CIK", "ORIGINATOR_CIK", "SPONSOR_CIK", "PERCENT_CA",
                 "WALA", "WAL", "INTEREST_DUE", "IPO_PRICE"}
NUMBER_FIELDS = {"DEAL_SIZE", "SERIES_SIZE", "POOL_SIZE", "TRANCHE_SIZE", "AOLS", "FICO_SCORE",
                 "LTV", "WA_COUPON", "WA_MATURITY_MONTHS", "INITIAL_NOTE_BALANCE", "NET_PROCEEDS",
                 "INT_RATE", "YIELD", "SPREAD_BPS", "CREDIT_ENHANCEMENT_PCT",
                 "UNDERWRITING_DISC_AND_COMMISIONS", "MINIMUM_PIECE", "MINIMUM_INCREMENT",
                 "ANNOUNCEMENT_DT", "EXPECTED_PRICING_DATE", "SETTLEMENT_DATE", "FIRST_COUPON_DT",
                 "MATURITY_DATE", "TRANCHE_SETTLEMENT_DATE", "ANT_REDEMPTION_DATE"}
VOCAB_FIELDS = {"ASSET_TYPE": "ASSET_TYPE", "DAY_COUNT": "DAY_COUNT",
                "ACCRUAL_METHOD": "ACCRUAL_METHOD", "BUSINESS_DAY_CONVENTION": "BUSINESS_DAY_CONVENTION",
                "RANKING": "RANKING", "COUPON_TYPE": "COUPON_TYPE"}


def assert_types(tag, node, path):
    for key, value in node.items():
        if key in ("SERIES", "TRANCHES", "SECURITIES"):
            continue
        if key in STRING_FIELDS:
            ok(f"{tag} §20.3.6 {path}.{key} is a string",
               isinstance(value, str), f"got {type(value).__name__} {value!r}")
        if key in NUMBER_FIELDS:
            ok(f"{tag} §20.3.6 {path}.{key} is a number",
               isinstance(value, (int, float)) and not isinstance(value, bool),
               f"got {type(value).__name__} {value!r}")
        if key in VOCAB_FIELDS:
            ok(f"{tag} §20.5.5 {path}.{key} is a vocabulary member",
               value in VOCAB[VOCAB_FIELDS[key]], f"{value!r} not in vocabularies.csv")


def assert_mandatory(tag, node, fields, path):
    for field in fields:
        value = node.get(field)
        ok(f"{tag} §20.3.7 {path}.{field} present and non-empty",
           value is not None and not (isinstance(value, str) and not value.strip()),
           f"got {value!r}")


def assert_payload(tag, payload, expect_currency=None):
    check(f"{tag} envelope MESSAGE_TYPE", payload["MESSAGE_TYPE"],
          "EVENT_CREATE_NEW_SECURITIZED_ISSUANCE")
    check(f"{tag} envelope SERVICE_NAME", payload["SERVICE_NAME"], "ISSUANCE_EVENT_HANDLER")
    check(f"{tag} envelope keys", sorted(payload), ["DETAILS", "MESSAGE_TYPE", "SERVICE_NAME"])

    details = payload["DETAILS"]
    ccy = details["CURRENCY_CODE"]
    if expect_currency:
        check(f"{tag} deal currency", ccy, expect_currency)
    assert_mandatory(tag, details, MANDATORY_DEAL, "$.DETAILS")
    assert_types(tag, details, "$.DETAILS")

    # §20.3.2 — the four deliberate schema misspellings must survive verbatim.
    for field in ("ORIGINATOR_MOODY_RATING", "ORIGINATOR_SANDP_RATING", "USER_OF_PROCEEDS"):
        ok(f"{tag} §20.3.2 {field} spelled verbatim", field in details, "field absent")
    for wrong in ("ORIGINATOR_MOODYS_RATING", "ORIGINATOR_SP_RATING", "USE_OF_PROCEEDS",
                  "ORIGINATOR_NAME"):
        ok(f"{tag} §20.3.2 {wrong} not emitted", wrong not in details, "corrected spelling leaked")

    country_ok = ccy != "EUR" or details["COUNTRY"] in ("ES", "IT", "FR", "DE", "NL", "IE")
    ok(f"{tag} §20.5.4 COUNTRY suits the currency", country_ok, f"{ccy} -> {details['COUNTRY']!r}")

    series_sum = 0
    for series_id, series in details["SERIES"].items():
        s_path = f"$.DETAILS.SERIES.{series_id}"
        check(f"{tag} §20.3.1 {series_id} SERIES_ID == map key", series["SERIES_ID"], series_id)
        assert_mandatory(tag, series, MANDATORY_SERIES, s_path)
        assert_types(tag, series, s_path)
        check(f"{tag} §20.6.6 {series_id} CURRENCY_CODE == deal", series["CURRENCY_CODE"], ccy)
        ok(f"{tag} §20.6.2 {series_id} POOL_SIZE > SERIES_SIZE",
           series["POOL_SIZE"] > series["SERIES_SIZE"],
           f"{series['POOL_SIZE']} vs {series['SERIES_SIZE']}")
        ok(f"{tag} §20.5.3 {series_id} PERCENT_CA is US-only",
           ccy == "USD" or "PERCENT_CA" not in series,
           f"{ccy} deal carries PERCENT_CA={series.get('PERCENT_CA')!r}")
        series_sum += series["SERIES_SIZE"]

        tranche_sum = 0
        ce_by_class = defaultdict(set)
        ratings_by_class = defaultdict(set)
        ranking_by_class = defaultdict(set)
        class_order = []
        maturities = []
        fixed_seq, float_seq = [], []

        for tranche_id, tranche in series["TRANCHES"].items():
            t_path = f"{s_path}.TRANCHES.{tranche_id}"
            check(f"{tag} §20.3.1 {tranche_id} TRANCHE_ID == map key", tranche["TRANCHE_ID"], tranche_id)
            check(f"{tag} §20.3.1 {tranche_id} SERIES_ID == enclosing key", tranche["SERIES_ID"], series_id)
            assert_mandatory(tag, tranche, MANDATORY_TRANCHE, t_path)
            assert_types(tag, tranche, t_path)
            check(f"{tag} §20.6.6 {tranche_id} CCY == deal currency", tranche["CCY"], ccy)

            size = tranche["TRANCHE_SIZE"]
            tranche_sum += size
            check(f"{tag} §20.6.1 {tranche_id} INITIAL_NOTE_BALANCE == TRANCHE_SIZE",
                  tranche["INITIAL_NOTE_BALANCE"], size)

            udc = tranche["UNDERWRITING_DISC_AND_COMMISIONS"]
            ok(f"{tag} §20.3.4 {tranche_id} UNDERWRITING_DISC_AND_COMMISIONS spelled verbatim",
               "UNDERWRITING_DISC_AND_COMMISSIONS" not in tranche, "double-S spelling leaked")
            expected_net = round(size * (1 - udc / 100.0), 2)
            ok(f"{tag} §20.6.5 {tranche_id} NET_PROCEEDS == size x (1 - udc/100)",
               abs(tranche["NET_PROCEEDS"] - expected_net) < 0.01,
               f"{tranche['NET_PROCEEDS']} != {expected_net}")

            spread = tranche["SPREAD_BPS"]
            if tranche["COUPON_TYPE"] == "Float":
                ok(f"{tag} §20.4.2 {tranche_id} INT_RATE omitted", "INT_RATE" not in tranche,
                   f"present: {tranche.get('INT_RATE')!r}")
                ok(f"{tag} §20.4.2 {tranche_id} YIELD omitted", "YIELD" not in tranche,
                   f"present: {tranche.get('YIELD')!r}")
                ok(f"{tag} §20.4.2 {tranche_id} BENCHMARK present", "BENCHMARK" in tranche)
                ok(f"{tag} §20.4.3 {tranche_id} SPREAD_BPS is a multiple of 10",
                   spread % 10 == 0, f"got {spread}")
                check(f"{tag} §20.4.2 {tranche_id} CURVE_REFERENCE agrees with SPREAD_BPS",
                      tranche["CURVE_REFERENCE"], f"{tranche['BENCHMARK']} + {spread} bps")
                float_seq.append((tranche["TRANCHE_CLASS"], spread))
            else:
                check(f"{tag} §20.6.5 {tranche_id} YIELD == INT_RATE",
                      tranche["YIELD"], tranche["INT_RATE"])
                ok(f"{tag} §20.4.1 {tranche_id} BENCHMARK omitted", "BENCHMARK" not in tranche)
                check(f"{tag} §20.4.1 {tranche_id} CURVE_REFERENCE agrees with SPREAD_BPS",
                      tranche["CURVE_REFERENCE"], f"I-Curve + {spread} bps")
                fixed_seq.append((tranche["TRANCHE_CLASS"], tranche["INT_RATE"], spread))

            check(f"{tag} {tranche_id} IPO_PRICE", tranche["IPO_PRICE"], "100")
            ok(f"{tag} §20.7.1 {tranche_id} ANT_REDEMPTION_DATE < MATURITY_DATE",
               tranche["ANT_REDEMPTION_DATE"] < tranche["MATURITY_DATE"])
            tenor_years = int(tranche["TENOR"].rstrip("Y"))
            ok(f"{tag} §20.7.2 {tranche_id} WAL < TENOR",
               float(tranche["WAL"]) < tenor_years, f"WAL {tranche['WAL']} vs {tranche['TENOR']}")
            maturities.append(tranche["MATURITY_DATE"])

            cls = tranche["TRANCHE_CLASS"]
            cc = cls.split("-", 1)[0]
            if cc not in class_order:
                class_order.append(cc)
            ce_by_class[cc].add(tranche["CREDIT_ENHANCEMENT_PCT"])
            ratings_by_class[cc].add((tranche["TRANCHE_FITCH"], tranche["TRANCHE_MOODYS"],
                                      tranche["TRANCHE_SP"]))
            ranking_by_class[cc].add(tranche["RANKING"])

            for security_id, security in tranche["SECURITIES"].items():
                c_path = f"{t_path}.SECURITIES.{security_id}"
                check(f"{tag} §20.3.1 {security_id} SECURITY_ID == map key",
                      security["SECURITY_ID"], security_id)
                check(f"{tag} §20.3.1 {security_id} TRANCHE_ID == enclosing key",
                      security["TRANCHE_ID"], tranche_id)
                check(f"{tag} §20.3.1 {security_id} SERIES_ID == enclosing key",
                      security["SERIES_ID"], series_id)
                check(f"{tag} §20.3.5 {tranche_id}/{security_id} SECURITY_CLASS == TRANCHE_CLASS",
                      security["SECURITY_CLASS"], cls)
                assert_mandatory(tag, security, MANDATORY_SECURITY, c_path)
                ok(f"{tag} §20.8.2 {security_id} ISIN is 12 chars",
                   len(security.get("ISIN", "")) == 12, f"got {security.get('ISIN')!r}")
                if ccy == "USD":
                    if security["REG_TYPE"] == "144A":
                        ok(f"{tag} §20.8.1 {security_id} 144A carries a 9-char CUSIP",
                           len(security.get("CUSIP", "")) == 9, f"got {security.get('CUSIP')!r}")
                    else:
                        ok(f"{tag} §20.3.5 {security_id} Reg S omits CUSIP",
                           "CUSIP" not in security, f"got {security.get('CUSIP')!r}")
                else:
                    ok(f"{tag} §20.5.4 {security_id} non-USD omits CUSIP", "CUSIP" not in security)
                    ok(f"{tag} §20.5.4 {security_id} non-USD ISIN is XS",
                       security["ISIN"].startswith("XS"), f"got {security['ISIN']!r}")

        check(f"{tag} §20.6.1 {series_id} SERIES_SIZE == sum of tranches",
              series["SERIES_SIZE"], tranche_sum)
        check(f"{tag} §20.7.1 {series_id} MATURITY_DATE == latest tranche + 1 day",
              series["MATURITY_DATE"], max(maturities) + 86_400_000)

        # §20.6.3 — the heart of it: CE is per CREDIT CLASS.
        for cc, values in ce_by_class.items():
            ok(f"{tag} §20.6.3 {series_id} class {cc}: one CREDIT_ENHANCEMENT_PCT",
               len(values) == 1, f"got {sorted(map(str, values))}")
            ok(f"{tag} §20.6.4 {series_id} class {cc}: one rating triplet",
               len(ratings_by_class[cc]) == 1, f"got {sorted(map(str, ratings_by_class[cc]))}")
            ok(f"{tag} §20.6.4 {series_id} class {cc}: one RANKING",
               len(ranking_by_class[cc]) == 1, f"got {sorted(ranking_by_class[cc])}")
        ce_seq = [next(iter(ce_by_class[cc])) for cc in class_order]
        ok(f"{tag} §20.6.3 {series_id} CE falls strictly across classes",
           all(a > b for a, b in zip(ce_seq, ce_seq[1:])), f"{class_order} -> {ce_seq}")

        # §20.6.3 arithmetic: CE(class) = 100 x (size junior to the class) / SERIES_SIZE,
        # except the most junior class, which carries the reserve.
        class_sizes = defaultdict(int)
        for tranche in series["TRANCHES"].values():
            class_sizes[tranche["TRANCHE_CLASS"].split("-", 1)[0]] += tranche["TRANCHE_SIZE"]
        for i, cc in enumerate(class_order[:-1]):
            junior = sum(class_sizes[c] for c in class_order[i + 1:])
            expected = round(100.0 * junior / series["SERIES_SIZE"], 2)
            ok(f"{tag} §20.6.3 {series_id} class {cc} CE == subordination below it",
               abs(ce_seq[i] - expected) < 0.011, f"got {ce_seq[i]}, expected {expected}")

        # §20.6.4 / §20.4.4 — monotonic rise, ranked within each coupon type independently.
        for a, b in zip(fixed_seq, fixed_seq[1:]):
            ok(f"{tag} §20.6.4 {series_id} fixed INT_RATE rises {a[0]}->{b[0]}", b[1] > a[1],
               f"{a[1]} -> {b[1]}")
            ok(f"{tag} §20.6.4 {series_id} fixed SPREAD_BPS rises {a[0]}->{b[0]}", b[2] > a[2],
               f"{a[2]} -> {b[2]}")
        for a, b in zip(float_seq, float_seq[1:]):
            ok(f"{tag} §20.6.4 {series_id} float SPREAD_BPS rises {a[0]}->{b[0]}", b[1] > a[1],
               f"{a[1]} -> {b[1]}")

        udc_seq = [t["UNDERWRITING_DISC_AND_COMMISIONS"] for t in tranches_of(series)]
        ok(f"{tag} §20.6.4 {series_id} UNDERWRITING_DISC rises down the stack",
           all(b > a for a, b in zip(udc_seq, udc_seq[1:])), f"{udc_seq}")
        wal_seq = [float(t["WAL"]) for t in tranches_of(series)]
        ok(f"{tag} §20.6.4 {series_id} WAL rises down the stack",
           all(b > a for a, b in zip(wal_seq, wal_seq[1:])), f"{wal_seq}")
        tenor_seq = [int(t["TENOR"].rstrip("Y")) for t in tranches_of(series)]
        ok(f"{tag} §20.7.2 {series_id} TENOR ascends strictly",
           all(b > a for a, b in zip(tenor_seq, tenor_seq[1:])), f"{tenor_seq}")

    check(f"{tag} §20.6.1 DEAL_SIZE == sum of series", details["DEAL_SIZE"], series_sum)

    # The engine's own pre-flight pass must agree — and must be silent.
    findings = E.check_payload(payload)
    check(f"{tag} check_payload errors", findings["errors"], [])
    check(f"{tag} check_payload warnings", findings["warnings"], [])


# ── the six shapes (§20.19.4) ──────────────────────────────────────────────────

SHAPES = [
    ("shape1", dict(deals=1, series_per_deal=[1], tranches_per_series=[1],
                    securities_per_tranche=[1], coupon_type="Fixed", currency="USD"),
     "1x1x1x1 Fixed USD — minimum tree"),
    ("shape2", dict(deals=1, series_per_deal=[1], tranches_per_series=[1],
                    securities_per_tranche=[1], coupon_type="Float", currency="USD"),
     "1x1x1x1 Float USD — BENCHMARK present, INT_RATE/YIELD absent"),
    ("shape3", dict(deals=1, series_per_deal=[1], tranches_per_series=[3],
                    securities_per_tranche=[2], coupon_type="Fixed", currency="USD"),
     "1x1x3x2 Fixed USD — the sample's shape"),
    ("shape4", dict(deals=1, series_per_deal=[1], tranches_per_series=[5],
                    securities_per_tranche=[2], coupon_type="Fixed", currency="USD"),
     "1x1x5x2 Fixed — A-1/A-2/A-3/B/C, CE equal across the A-x"),
    ("shape5", dict(deals=1, series_per_deal=[2], tranches_per_series=[3],
                    securities_per_tranche=[2], coupon_type="Mixed", currency="USD"),
     "1x2x3x2 Mixed — per-parent TEMP numbering, ladders ranked per coupon type"),
    ("shape6", dict(deals=1, series_per_deal=[1], tranches_per_series=[3],
                    securities_per_tranche=[2], coupon_type="Fixed", currency="EUR"),
     "1x1x3x2 Fixed EUR — no CUSIP, XS ISINs, euro-area COUNTRY, no PERCENT_CA"),
]

FIRST_PAYLOADS = {}

for name, params, description in SHAPES:
    group(f"{name} — {description}  ({REPEATS} runs)")
    for run_index in range(REPEATS):
        payloads, logs, summary = run_engine(**params)
        tag = f"{name}#{run_index}"
        check(f"{tag} one payload emitted", len(payloads), 1)
        if not payloads:
            continue
        assert_payload(tag, payloads[0], expect_currency=params["currency"])
        if run_index == 0:
            FIRST_PAYLOADS[name] = payloads[0]
            check(f"{tag} summary counts the dry run",
                  (summary["success"], summary["failed"]), (1, 0))
            ok(f"{tag} no error-level log lines",
               not [l for l in logs if l["level"] == "error"],
               str([l["msg"] for l in logs if l["level"] == "error"][:2]))
    print(f"  ({REPEATS} runs asserted)")


# ── shape-specific structure ───────────────────────────────────────────────────

group("shape-specific structure")

d1 = FIRST_PAYLOADS["shape1"]["DETAILS"]
check("shape1 one series", list(d1["SERIES"]), ["TEMP-Series-0"])
s1 = d1["SERIES"]["TEMP-Series-0"]
check("shape1 one tranche", list(s1["TRANCHES"]), ["TEMP-Tranches-0"])
check("shape1 one security", list(s1["TRANCHES"]["TEMP-Tranches-0"]["SECURITIES"]),
      ["TEMP-Securities-0"])
check("shape1 single tranche is class A",
      s1["TRANCHES"]["TEMP-Tranches-0"]["TRANCHE_CLASS"], "A")

d4 = FIRST_PAYLOADS["shape4"]["DETAILS"]
s4 = d4["SERIES"]["TEMP-Series-0"]
classes4 = [t["TRANCHE_CLASS"] for t in tranches_of(s4)]
check("shape4 §20.6.4 five-tranche class ladder", classes4, ["A-1", "A-2", "A-3", "B", "C"])
ce4 = {t["TRANCHE_CLASS"]: t["CREDIT_ENHANCEMENT_PCT"] for t in tranches_of(s4)}
ok("shape4 §20.6.3 CE equal across A-1/A-2/A-3",
   ce4["A-1"] == ce4["A-2"] == ce4["A-3"], f"{ce4}")
ok("shape4 §20.6.3 CE(A) > CE(B) > CE(C)",
   ce4["A-1"] > ce4["B"] > ce4["C"], f"{ce4}")
ratings4 = {t["TRANCHE_CLASS"]: t["TRANCHE_FITCH"] for t in tranches_of(s4)}
ok("shape4 §20.6.4 A-x share one rating", len({ratings4["A-1"], ratings4["A-2"], ratings4["A-3"]}) == 1,
   f"{ratings4}")
check("shape4 §20.6.4 senior stack is AAA", ratings4["A-1"], "AAA")
cusips4 = [s["CUSIP"] for t in tranches_of(s4) for s in t["SECURITIES"].values() if "CUSIP" in s]
check("shape4 §20.8.1 one CUSIP suffix per tranche, in stack order",
      [c[6:8] for c in cusips4], ["AA", "AB", "AC", "AD", "AE"])
check("shape4 §20.8.1 one CUSIP base per deal", len({c[:6] for c in cusips4}), 1)

d5 = FIRST_PAYLOADS["shape5"]["DETAILS"]
check("shape5 two series", sorted(d5["SERIES"]), ["TEMP-Series-0", "TEMP-Series-1"])
for sid, series in d5["SERIES"].items():
    check(f"shape5 §20.3.1 {sid} tranche keys restart at 0",
          sorted(series["TRANCHES"], key=lambda s: int(s.rsplit("-", 1)[1])),
          ["TEMP-Tranches-0", "TEMP-Tranches-1", "TEMP-Tranches-2"])
    for tid, tranche in series["TRANCHES"].items():
        check(f"shape5 §20.3.1 {sid}/{tid} security keys restart at 0",
              sorted(tranche["SECURITIES"], key=lambda s: int(s.rsplit("-", 1)[1])),
              ["TEMP-Securities-0", "TEMP-Securities-1"])
all_isins5 = [s["ISIN"] for ser in d5["SERIES"].values() for t in ser["TRANCHES"].values()
              for s in t["SECURITIES"].values()]
check("shape5 §20.8.4 ISINs unique across both series", len(set(all_isins5)), len(all_isins5))
ann5 = [ser for ser in series_of(d5)]
ok("shape5 §20.7.3 series 2 settles after series 1",
   ann5[1]["SETTLEMENT_DATE"] > ann5[0]["SETTLEMENT_DATE"],
   f"{ann5[0]['SETTLEMENT_DATE']} vs {ann5[1]['SETTLEMENT_DATE']}")
check("shape5 §20.3.3 both series share the deal's ASSET_TYPE",
      len({ser["ASSET_TYPE"] for ser in ann5}), 1)

d6 = FIRST_PAYLOADS["shape6"]["DETAILS"]
s6 = d6["SERIES"]["TEMP-Series-0"]
check("shape6 §20.5.4 EUR DAY_COUNT is currency-driven",
      s6["DAY_COUNT"], CURRENCIES["EUR"]["DAY_COUNT"])
ok("shape6 §20.5.4 every security is Reg S",
   {s["REG_TYPE"] for t in tranches_of(s6) for s in t["SECURITIES"].values()} == {"Reg S"})
ok("shape6 §20.5.4 GOVERNING_LAW from the EUR list",
   s6["GOVERNING_LAW"] in CURRENCIES["EUR"]["GOVERNING_LAW"].split("|"), s6["GOVERNING_LAW"])

d2 = FIRST_PAYLOADS["shape2"]["DETAILS"]
t2 = d2["SERIES"]["TEMP-Series-0"]["TRANCHES"]["TEMP-Tranches-0"]
check("shape2 §20.4.3 float senior starts at 20 bps", t2["SPREAD_BPS"], 20)
check("shape2 §20.5.4 USD float BENCHMARK", t2["BENCHMARK"], "SOFR")


# ── cross-cutting: multi-deal run, identifier uniqueness, cycling lists ────────

group("cross-cutting")

payloads, logs, summary = run_engine(deals=6, series_per_deal="1,2,3",
                                     tranches_per_series="1,3,2",
                                     securities_per_tranche="2", coupon_type="Mixed")
check("6-deal run emits 6 payloads", len(payloads), 6)
check("6-deal summary", (summary["success"], summary["failed"]), (6, 0))
series_counts = [len(p["DETAILS"]["SERIES"]) for p in payloads]
check("§20.9 series_per_deal cycles 1,2,3", series_counts, [1, 2, 3, 1, 2, 3])
tranche_counts = [len(s["TRANCHES"]) for p in payloads for s in series_of(p["DETAILS"])]
check("§20.9 tranches_per_series cycles across every series in the run",
      tranche_counts, [1, 3, 2, 1, 3, 2, 1, 3, 2, 1, 3, 2])

all_isins = [s["ISIN"] for p in payloads for ser in p["DETAILS"]["SERIES"].values()
             for t in ser["TRANCHES"].values() for s in t["SECURITIES"].values()]
check("§20.8.4 ISINs unique across the whole run", len(set(all_isins)), len(all_isins))
all_cusips = [s["CUSIP"] for p in payloads for ser in p["DETAILS"]["SERIES"].values()
              for t in ser["TRANCHES"].values() for s in t["SECURITIES"].values() if "CUSIP" in s]
check("§20.8.4 CUSIPs unique across the whole run", len(set(all_cusips)), len(all_cusips))
all_figis = [s["FIGI"] for p in payloads for ser in p["DETAILS"]["SERIES"].values()
             for t in ser["TRANCHES"].values() for s in t["SECURITIES"].values() if "FIGI" in s]
check("§20.8.4 FIGIs unique across the whole run", len(set(all_figis)), len(all_figis))
for p in payloads:
    assert_payload("multi", p)

coupons = {t["COUPON_TYPE"] for p in payloads for ser in p["DETAILS"]["SERIES"].values()
           for t in ser["TRANCHES"].values()}
check("§20.4.4 Mixed produces both coupon types", coupons, {"Fixed", "Float"})

# §20.9 — a value beyond the realistic range is allowed, not clamped, but warns.
payloads, logs, summary = run_engine(deals=1, series_per_deal=[5], tranches_per_series=[8],
                                     securities_per_tranche=[3], currency="GBP")
warns = " | ".join(l["msg"] for l in logs if l["level"] == "warn")
ok("§20.9 >4 series warns but proceeds", "exceeds the realistic max of 4" in warns, warns[:160])
ok("§20.9 >6 tranches warns but proceeds", "exceeds the realistic max of 6" in warns, warns[:160])
ok("§20.9 securities_per_tranche clamped to 1..2", "clamped to [2]" in warns, warns[:160])
check("§20.9 the oversized deal still generates", len(payloads), 1)
check("§20.9 5 series were generated", len(payloads[0]["DETAILS"]["SERIES"]), 5)
assert_payload("oversized", payloads[0], expect_currency="GBP")
classes8 = [t["TRANCHE_CLASS"] for t in tranches_of(series_of(payloads[0]["DETAILS"])[0])]
check("§20.6.4 8-tranche layout extends past the table",
      classes8, ["A-1", "A-2", "A-3", "A-4", "B", "C", "D", "E"])

# §20.11 — dry run does not touch the network. If it did, host "unused" would raise.
ok("§20.11 dry run needs no auth and no POST", True)

# Unknown currency is a hard stop; unknown asset type degrades to random.
payloads, logs, summary = run_engine(deals=1, currency="JPY")
check("unknown currency aborts the run", len(payloads), 0)
ok("unknown currency logs an error",
   any(l["level"] == "error" and "Unknown currency" in l["msg"] for l in logs))
payloads, logs, summary = run_engine(deals=1, asset_type="Nonexistent ABS", currency="USD")
check("unknown asset_type still generates", len(payloads), 1)
ok("unknown asset_type warns",
   any(l["level"] == "warn" and "Unknown asset_type" in l["msg"] for l in logs))
# PRICING_SPEED varies its number and keeps its convention suffix (slice-1 caveat).
speeds = set()
for _ in range(20):
    p, _l, _s = run_engine(deals=1, asset_type="Auto Loan ABS", currency="USD")
    for ser in p[0]["DETAILS"]["SERIES"].values():
        for t in ser["TRANCHES"].values():
            speeds.add(t["PRICING_SPEED"])
ok("PRICING_SPEED keeps its convention suffix", all(s.endswith("% ABS") for s in speeds), str(speeds))
ok("PRICING_SPEED varies its numeric part", len(speeds) > 1, str(speeds))
ok("PRICING_SPEED stays near the reference quote (1.30% ABS)",
   all(1.0 <= float(s.split("%")[0]) <= 1.6 for s in speeds), str(sorted(speeds)))
# The 20 asset types with no prepayment convention omit both fields rather than
# emitting the "None" / "0% CPR" sentinel (§20.5.3, slice-2 addendum).
p, _l, _s = run_engine(deals=1, asset_type="Aircraft Lease ABS", currency="USD")
for ser in p[0]["DETAILS"]["SERIES"].values():
    for tid, t in ser["TRANCHES"].items():
        ok(f"§20.5.3 {tid} omits PREPAYMENT_TYPE when the sentinel applies",
           "PREPAYMENT_TYPE" not in t, f"got {t.get('PREPAYMENT_TYPE')!r}")
        ok(f"§20.5.3 {tid} omits PRICING_SPEED when the sentinel applies",
           "PRICING_SPEED" not in t, f"got {t.get('PRICING_SPEED')!r}")

payloads, logs, summary = run_engine(deals=1, coupon_type="Step", currency="USD")
ok("unsupported coupon_type falls back to Fixed with a warn",
   any(l["level"] == "warn" and "Unsupported coupon_type" in l["msg"] for l in logs))
check("unsupported coupon_type still generates", len(payloads), 1)

# A forced asset type must be honoured and its family must drive the entity pick.
ENTITY_FAMILY = {r["ISSUER_NAME"]: r["ASSET_FAMILY"] for r in load_csv("entities.csv")}
ASSET_FAMILY = {r["ASSET_TYPE"]: r["ASSET_FAMILY"] for r in load_csv("asset_types.csv")}
payloads, logs, summary = run_engine(deals=3, asset_type="Credit Card ABS", currency="USD")
for p in payloads:
    details = p["DETAILS"]
    check("forced asset_type is used",
          {s["ASSET_TYPE"] for s in details["SERIES"].values()}, {"Credit Card ABS"})
    stem = re.sub(r" \d{4}-\d+$", "", details["ISSUER_NAME"])
    check("§20.5.2 the entity family matches the forced asset type",
          ENTITY_FAMILY.get(stem), ASSET_FAMILY["Credit Card ABS"])

# §20.10.1 — the log shape a NACK gets read against.
payloads, logs, summary = run_engine(deals=1, series_per_deal=[1], tranches_per_series=[3],
                                     securities_per_tranche=[2], coupon_type="Mixed", currency="USD")
lines = [l["msg"] for l in logs]
ok("§20.10.1 log leads with ISSUER_NAME (TICKER)",
   any(re.match(r"\[D1\] .+ \(\w+\) — USD · .+ · [\d,]+$", m) for m in lines),
   str([m for m in lines if m.startswith("[D1] ")][:2]))
ok("§20.10.1 a series line is logged",
   any(m.startswith('[D1]   Series TEMP-Series-0 "') for m in lines))
ok("§20.10.1 the structure is logged before the payload",
   next(i for i, m in enumerate(lines) if m.startswith("[D1]   Series"))
   < next(i for i, m in enumerate(lines) if m.startswith("{")))
ok("§20.10.1 a float tranche prints its benchmark where fixed prints a coupon",
   all(("SOFR" in m) or ("%" in m) for m in lines
       if re.match(r"\[D1\]     [A-D](-\d)? ", m)),
   str([m for m in lines if re.match(r"\[D1\]     [A-D]", m)]))

# The pre-flight pass must actually fire on a broken payload, not just stay quiet.
group("check_payload catches what it claims to")
broken = json.loads(json.dumps(FIRST_PAYLOADS["shape3"]))
broken["DETAILS"]["ISSUER_TICKER"] = ""
ok("mandatory field emptied -> error",
   any("ISSUER_TICKER" in e for e in E.check_payload(broken)["errors"]))
# shape4 is the one with A-1/A-2/A-3, so this is the per-tranche-CE bug §20.19.4 warns about.
broken = json.loads(json.dumps(FIRST_PAYLOADS["shape4"]))
a1 = broken["DETAILS"]["SERIES"]["TEMP-Series-0"]["TRANCHES"]["TEMP-Tranches-0"]
a1["CREDIT_ENHANCEMENT_PCT"] = a1["CREDIT_ENHANCEMENT_PCT"] + 3
ok("CE computed per tranche instead of per class -> warning",
   any("CREDIT_ENHANCEMENT_PCT differs" in w for w in E.check_payload(broken)["warnings"]),
   str(E.check_payload(broken)["warnings"][:2]))
broken = json.loads(json.dumps(FIRST_PAYLOADS["shape3"]))
last_id = sorted(broken["DETAILS"]["SERIES"]["TEMP-Series-0"]["TRANCHES"])[-1]
broken["DETAILS"]["SERIES"]["TEMP-Series-0"]["TRANCHES"][last_id]["CREDIT_ENHANCEMENT_PCT"] = 99.0
ok("CE not falling across classes -> warning",
   any("must fall across classes" in w for w in E.check_payload(broken)["warnings"]))
broken = json.loads(json.dumps(FIRST_PAYLOADS["shape3"]))
first_tranche = next(iter(broken["DETAILS"]["SERIES"]["TEMP-Series-0"]["TRANCHES"].values()))
first_tranche["TRANCHE_SIZE"] = first_tranche["TRANCHE_SIZE"] + 100
ok("size roll-up broken -> warning",
   any("SERIES_SIZE" in w for w in E.check_payload(broken)["warnings"]))
broken = json.loads(json.dumps(FIRST_PAYLOADS["shape3"]))
first_security = next(iter(next(iter(
    broken["DETAILS"]["SERIES"]["TEMP-Series-0"]["TRANCHES"].values()))["SECURITIES"].values()))
first_security["SECURITY_CLASS"] = "Z"
ok("SECURITY_CLASS mismatch -> warning",
   any("SECURITY_CLASS" in w for w in E.check_payload(broken)["warnings"]))
broken = json.loads(json.dumps(FIRST_PAYLOADS["shape3"]))
first_tranche = next(iter(broken["DETAILS"]["SERIES"]["TEMP-Series-0"]["TRANCHES"].values()))
first_tranche["WAL"] = 1.40
ok("WAL sent as a number -> §20.3.6 warning",
   any("WAL" in w and "string" in w for w in E.check_payload(broken)["warnings"]))

# The engine must skip the POST when the pre-flight pass finds a mandatory-field failure.
group("pre-flight failure skips the deal")
_original = E.DealGenerator.generate_deal


def _blank_ticker(self, *a, **kw):
    deal = _original(self, *a, **kw)
    deal["fields"]["ISSUER_TICKER"] = ""
    return deal


E.DealGenerator.generate_deal = _blank_ticker
try:
    payloads, logs, summary = run_engine(deals=1, currency="USD")
finally:
    E.DealGenerator.generate_deal = _original
check("a payload failing §20.3.7 is not dumped", len(payloads), 0)
check("and is counted as failed", (summary["success"], summary["failed"]), (0, 1))
ok("and says why", any("mandatory field missing" in l["msg"] for l in logs))


# ── result ─────────────────────────────────────────────────────────────────────

print(f"\n{'=' * 78}")
print(f"{PASS}/{PASS + FAIL} pass" + (f"   {FAIL} FAILED" if FAIL else ""))
for f in FAILURES[:20]:
    print(f"  - {f}")
print("=" * 78)
sys.exit(1 if FAIL else 0)
