"""Dry-run assertions for the Munis engine — spec §21.19.

    cd backend
    .\\.venv\\Scripts\\python.exe tests\\test_munis.py

No pytest, no server, no network — matching the other files in this directory. The engine
is driven exactly as `CLAUDE.md` prescribes for an engine smoke test: a real
``queue.Queue`` + ``threading.Event`` with ``dry_run: True``, then the emitted payloads are
pulled back off the queue and asserted.

Every shape is run REPEATS times, because the generator is deliberately unseeded — a
single pass would only prove one draw is consistent, and the issuer-dependency rules of
§21.6 are exactly the kind that hold for most draws.

Assertions recompute the §21.6 arithmetic **independently** rather than calling the
engine's own ``check_payload``; that checker is then run as well and required to be
silent, so a bug in the checker cannot hide a bug in the generator (and vice versa).
"""
from __future__ import annotations

import json
import queue
import re
import sys
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engines.munis_engine import (  # noqa: E402
    ENHANCEMENT_STATES, MATURITY_QUANTUM, REPAYMENT_SECTORS, STATUS_WIRE_TYPES,
    ReferenceData, check_payload, deal_ratings, run_munis,
)

REF_DIR = str(Path(__file__).resolve().parent.parent / "reference" / "munis")
REPEATS = 6

SHAPES = [
    {"deals": 2, "series_per_deal": [1], "maturities_per_series": [1]},
    {"deals": 2, "series_per_deal": [2], "maturities_per_series": [5, 8]},
    {"deals": 2, "series_per_deal": [3], "maturities_per_series": [12]},
    {"deals": 1, "series_per_deal": [7], "maturities_per_series": [31]},
    {"deals": 2, "series_per_deal": [2], "maturities_per_series": [6], "state": "PA"},
    {"deals": 2, "series_per_deal": [2], "maturities_per_series": [6], "tax_status": "Taxable"},
    # "Priced" and "Expected" appear in MUNIS_DATA but not in the platform's own
    # drop-down list, so they are read as derived states and are not offered here.
    {"deals": 2, "series_per_deal": [2], "maturities_per_series": [6],
     "deal_status": "Allotments Available"},
    # §21.6.13 — both roadshow branches, explicitly. A 20% draw would exercise the
    # MANDATE_TEXT rule only intermittently, which is how it reached a live NACK.
    {"deals": 2, "series_per_deal": [1], "maturities_per_series": [3], "roadshow": "yes"},
    {"deals": 2, "series_per_deal": [1], "maturities_per_series": [3], "roadshow": "no"},
]

FAILURES: list = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILURES.append(msg)


def generate(params: dict) -> list:
    """Run the engine in dry-run and return the payloads it logged."""
    q: queue.Queue = queue.Queue()
    run_munis({**params, "delay": 0, "dry_run": True, "ref_dir": REF_DIR},
              {}, threading.Event(), q)
    payloads, errors = [], []
    while True:
        item = q.get()
        if item is None:
            break
        if item.get("type") != "log":
            continue
        msg = item["msg"]
        if msg.startswith("{"):
            payloads.append(json.loads(msg))
        elif item["level"] == "error":
            errors.append(msg)
    check(not errors, f"engine logged errors: {errors[:3]}")
    return payloads


def as_date(ms: int):
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def assert_payload(p: dict, params: dict, ref: ReferenceData) -> None:
    check(set(p) == {"DETAILS", "MESSAGE_TYPE", "SESSION_AUTH_TOKEN", "SOURCE_REF"},
          f"envelope keys are {sorted(p)}")
    check(p["MESSAGE_TYPE"] == "EVENT_CREATE_NEW_MUNIS_ISSUANCE", "wrong MESSAGE_TYPE")
    check(re.fullmatch(r"[0-9a-f-]{36}", p["SOURCE_REF"]) is not None,
          f"SOURCE_REF is not a UUID: {p['SOURCE_REF']!r}")

    d = p["DETAILS"]
    check(isinstance(d["SERIES"], list) and d["SERIES"], "SERIES must be a non-empty array")

    # §21.6.9 — the two roll-ups, recomputed here rather than trusted.
    total = 0
    for s in d["SERIES"]:
        check(isinstance(s["TRANCHES"], list), "TRANCHES must be an array")
        mat_sum = sum(m["MATURITY_AMOUNT"] for m in s["TRANCHES"])
        check(s["SERIES_SIZE"] == 1000 * mat_sum,
              f"SERIES_SIZE {s['SERIES_SIZE']} != 1000 * {mat_sum}")
        total += s["SERIES_SIZE"]
        for m in s["TRANCHES"]:
            check(m["MATURITY_AMOUNT"] % MATURITY_QUANTUM == 0,
                  f"MATURITY_AMOUNT {m['MATURITY_AMOUNT']} is not a $5k multiple")
            check(m["MATURITY_AMOUNT"] > 0, "MATURITY_AMOUNT must be positive")
    check(d["DEAL_SIZE"] == total, f"DEAL_SIZE {d['DEAL_SIZE']} != sum(SERIES_SIZE) {total}")

    # §21.6.13 — MANDATE_TEXT is present exactly when IS_ROADSHOW is true. Asserted as
    # an equivalence, not an implication: sending it on a non-roadshow deal is the other
    # way to get this wrong.
    check(("MANDATE_TEXT" in d) == (d["IS_ROADSHOW"] is True),
          f"IS_ROADSHOW={d['IS_ROADSHOW']} but MANDATE_TEXT "
          f"{'present' if 'MANDATE_TEXT' in d else 'absent'}")
    if d["IS_ROADSHOW"]:
        check(len(d.get("MANDATE_TEXT", "").strip()) > 20, "MANDATE_TEXT is too short to be real")
    if params.get("roadshow") in ("yes", True):
        check(d["IS_ROADSHOW"] is True, "roadshow=yes ignored")
    if params.get("roadshow") in ("no", False):
        check(d["IS_ROADSHOW"] is False, "roadshow=no ignored")

    # §21.6.4 — status/wire pairing.
    check(d["WIRE_TYPE"] in STATUS_WIRE_TYPES[d["DEAL_STATUS"]],
          f"WIRE_TYPE {d['WIRE_TYPE']!r} not observed with {d['DEAL_STATUS']!r}")

    # §21.6.8 — every date is UTC midnight, and the calendar runs forward.
    for key in ("ANNOUNCEMENT_DT", "EXPECTED_PRICING_DATE",
                "ORDER_PERIOD_BEGIN_DATE", "ORDER_PERIOD_END_DATE"):
        dt = as_date(d[key])
        check((dt.hour, dt.minute, dt.second) == (0, 0, 0), f"{key} is not UTC midnight")
    check(d["ANNOUNCEMENT_DT"] < d["EXPECTED_PRICING_DATE"] < d["ORDER_PERIOD_BEGIN_DATE"]
          < d["ORDER_PERIOD_END_DATE"], "deal calendar is out of order")

    for s in d["SERIES"]:
        check(s["TRADE_DATE"] <= s["DATED_DATE"] <= s["DELIVERY_DATE"] <= s["FIRST_COUPON_DATE"],
              "series calendar is out of order")
        check(s["TRADE_DESK"] == "Munis", "TRADE_DESK must be Munis")
        check(s["SERIES_STATUS"] == "ACTIVE", "SERIES_STATUS must be ACTIVE")

        # §21.6.2 — purpose and sector belong to the same family.
        if s["PURPOSE"] and s["SECTOR"]:
            mapped = ref.purpose_sector.get(s["PURPOSE"])
            check(mapped in (None, s["SECTOR"]),
                  f"PURPOSE {s['PURPOSE']} maps to {mapped}, not {s['SECTOR']}")

        # §21.6.3 — repayment source only on GO-style sectors.
        if s.get("SOURCE_OF_REPAYMENT"):
            check(s["SECTOR"] in REPAYMENT_SECTORS,
                  f"repayment source on a {s['SECTOR']!r} series")

        # §21.6.6 — enhancement must be legal in the deal's state.
        enh = s["ENHANCEMENT"]
        if enh and enh != "None":
            states = ENHANCEMENT_STATES.get(enh)
            check(states is None or d["STATE"] in states,
                  f"{enh!r} on a {d['STATE']} deal")

        # §21.6.11 — the ladder ascends, and every maturity outlives delivery.
        dates = [m["MATURITY_DATE"] for m in s["TRANCHES"]]
        check(dates == sorted(dates) and len(set(dates)) == len(dates),
              "maturity ladder does not strictly ascend")
        check(all(x > s["DELIVERY_DATE"] for x in dates), "a maturity precedes delivery")

        # §21.6.12 — price follows from coupon vs yield.
        for m in s["TRANCHES"]:
            if m["PRICE"] > 100:
                check(m["YIELD"] < m["COUPON"],
                      f"premium price {m['PRICE']} with yield {m['YIELD']} >= "
                      f"coupon {m['COUPON']}")
            elif m["PRICE"] == 100:
                check(abs(m["YIELD"] - m["COUPON"]) < 0.002,
                      f"par price with yield {m['YIELD']} != coupon {m['COUPON']}")

        # §21.6.5 — the description form is chosen by tax status, never at random.
        for m in s["TRANCHES"]:
            desc = m["MATURITY_DESCRIPTION"]
            if "bps over yld" in desc:
                check(s["TAX_STATUS"] in ("Taxable", "Various"),
                      f"treasury-spread description on a {s['TAX_STATUS']!r} series")
                check(re.fullmatch(
                    r"[\d.]+ bps over yld  of [\d.]+ cpn \d{2}/(\d{2}/)?\d{4} tsy", desc),
                    f"malformed treasury description: {desc!r}")
            elif "Maturity " in desc:
                year = as_date(m["MATURITY_DATE"]).year
                check(desc == f"{s['SERIES_DESCRIPTION']} Maturity {year}",
                      f"series-form description does not match its series/year: {desc!r}")

    # CUSIPs are unique across the whole deal (§21.8.1).
    cusips = [sec["CUSIP"] for s in d["SERIES"] for m in s["TRANCHES"]
              for sec in m["SECURITIES"]]
    check(len(cusips) == len(set(cusips)), "duplicate CUSIP within a deal")
    check(all(len(c) == 9 for c in cusips), "a CUSIP is not 9 characters")

    # Filters actually filter.
    if params.get("state"):
        check(d["STATE"] == params["state"], f"state filter ignored: {d['STATE']}")
    if params.get("tax_status"):
        check(all(s["TAX_STATUS"] == params["tax_status"] for s in d["SERIES"]),
              "tax_status filter ignored")
    if params.get("deal_status"):
        check(d["DEAL_STATUS"] == params["deal_status"], "deal_status filter ignored")

    # The engine's own checker must agree the payload is clean.
    findings = check_payload(p)
    check(not findings["errors"], f"check_payload errors: {findings['errors'][:3]}")
    check(not findings["warnings"], f"check_payload warnings: {findings['warnings'][:3]}")


def main() -> int:
    ref = ReferenceData(REF_DIR)
    check(not ref.missing, f"reference files missing: {ref.missing}")

    rating_kinds: Counter = Counter()
    n = 0
    for shape in SHAPES:
        for _ in range(REPEATS):
            for payload in generate(dict(shape)):
                assert_payload(payload, shape, ref)
                rating_kinds["Various" if deal_ratings(payload["DETAILS"])[0] == "Various"
                             else "single"] += 1
                n += 1

    # §21.6.10 — "Various" is meant to be the exception, as it is in the source
    # (10 of 95 deals). A regression that notches every series would show up here.
    various = rating_kinds["Various"] / max(1, n)
    check(various < 0.35, f"{various:.0%} of deals rate Various — notching is too eager")

    print(f"munis: {n} payloads asserted across {len(SHAPES)} shapes x {REPEATS} repeats")
    print(f"       deal ratings: {dict(rating_kinds)} ({various:.0%} Various)")
    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in dict.fromkeys(FAILURES):
            print("  -", f)
        return 1
    print("       all §21.6 consistency rules hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
